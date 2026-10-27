import logging
import queue
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .runner import run_render
from .source import ProjectError, SourceNotFound, fetch_project
from .validation import cli_args
from .storage import TooManyObjects

log = logging.getLogger("render")

TERMINAL = ("done", "failed")


class QueueFull(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, storage, fetch=fetch_project, run=run_render):
        self.storage = storage
        self.fetch = fetch
        self.run = run
        self._q: queue.Queue[dict | None] = queue.Queue()
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _exists(self, folder: str) -> bool:
        bucket = config.SOURCE_BUCKET
        if folder.endswith(".zip"):
            return self.storage.head_size(bucket, folder) is not None
        prefix = folder.rstrip("/") + "/"
        try:
            keys = self.storage.list_keys(bucket, prefix, config.RENDER_MAX_FILES)
        except TooManyObjects:
            return True
        out_prefix = prefix + config.RENDER_PREFIX.strip("/") + "/"
        return any(not k.startswith(out_prefix) for k, _ in keys)

    def _persist(self, job: dict) -> None:
        job["updated_at"] = _now()
        try:
            self.storage.put_json(config.S3_BUCKET, f"jobs/{job['job_id']}.json", job)
        except Exception:
            log.exception("failed to persist job %s", job["job_id"])

    def _set(self, job: dict, status: str, **kw) -> None:
        job.update(status=status, **kw)
        self._persist(job)

    def _full(self) -> bool:
        return len(self._active) >= config.RENDER_QUEUE_MAX

    def submit(self, folder: str, name: str, quality: str, fps: int, workers: str | int) -> dict:
        with self._lock:
            if self._full():
                raise QueueFull()
        if not self._exists(folder):
            raise SourceNotFound(folder)
        now = _now()
        job = {
            "job_id": uuid.uuid4().hex, "status": "queued", "folder": folder, "name": name,
            "quality": quality, "fps": fps, "workers": workers, "url": None, "render_s": None,
            "error": None, "log_tail": None, "created_at": now, "updated_at": now,
        }
        with self._lock:
            if self._full():
                raise QueueFull()
            self._active.add(job["job_id"])
        try:
            job["updated_at"] = _now()
            self.storage.put_json(config.S3_BUCKET, f"jobs/{job['job_id']}.json", job)
        except Exception:
            with self._lock:
                self._active.discard(job["job_id"])
            raise
        self._q.put(job)
        return dict(job)

    def get(self, job_id: str) -> dict | None:
        job = self.storage.get_json(config.S3_BUCKET, f"jobs/{job_id}.json")
        if job is None:
            return None
        with self._lock:
            active = job_id in self._active
        if job.get("status") not in TERMINAL and not active:
            job.update(status="failed", error="interrupted (service restarted)")
        return job

    def _render(self, job: dict) -> None:
        folder = job["folder"]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._set(job, "downloading")
            root = self.fetch(
                self.storage, config.SOURCE_BUCKET, folder, tmp,
                config.RENDER_MAX_DOWNLOAD_MB * 1024 * 1024, config.RENDER_MAX_FILES,
            )
            out = tmp / "out.mp4"
            self._set(job, "rendering")
            t0 = time.monotonic()
            res = self.run(root, cli_args(job["quality"], job["fps"], job["workers"], out), config.RENDER_TIMEOUT_S)
            elapsed = round(time.monotonic() - t0, 1)
            if res.timed_out:
                self._set(job, "failed", error=f"render timed out after {config.RENDER_TIMEOUT_S}s", log_tail=res.log_tail)
                return
            if res.returncode != 0:
                self._set(job, "failed", error=f"hyperframes exited {res.returncode}", log_tail=res.log_tail)
                return
            if not out.is_file() or out.stat().st_size == 0:
                self._set(job, "failed", error="render produced no output", log_tail=res.log_tail)
                return
            self._set(job, "uploading", render_s=elapsed)
            base = folder.rstrip("/")
            if base.endswith(".zip"):
                base = base[:-4]
            key = f"{config.RENDER_PREFIX.strip('/')}/{base}/{job['name']}-{job['job_id'][:8]}.mp4"
            self.storage.upload_file(config.S3_BUCKET, key, out, "video/mp4")
            self._set(job, "done", url=self.storage.url(key))

    def _process(self, job: dict) -> None:
        try:
            self._render(job)
        except (SourceNotFound, ProjectError) as e:
            self._set(job, "failed", error=str(e))
        except Exception as e:
            log.exception("render job %s crashed", job["job_id"])
            self._set(job, "failed", error=f"internal error: {type(e).__name__}")
        finally:
            with self._lock:
                self._active.discard(job["job_id"])

    def run_pending(self) -> None:
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                return
            if job is not None:
                self._process(job)

    def _loop(self) -> None:
        while (job := self._q.get()) is not None:
            self._process(job)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="render-worker")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._q.put(None)
            self._thread.join(timeout=5)
            self._thread = None
