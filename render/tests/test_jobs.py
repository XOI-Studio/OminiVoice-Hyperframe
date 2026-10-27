import os
import threading
import time
from pathlib import Path

import pytest

from app import config
from app.jobs import JobManager, QueueFull
from app.runner import RunResult
from app.source import ProjectError, SourceNotFound
from app.storage import TooManyObjects


class FakeStorage:
    def __init__(self, keys=None, sizes=None, fail_put=False, fail_after=0):
        self.keys = keys or []
        self.sizes = sizes or {}
        self.fail_put = fail_put
        self.fail_after = fail_after  # with fail_put: let this many puts succeed first
        self.puts = 0
        self.listings = 0
        self.objs: dict[str, dict] = {}
        self.history: list[str] = []
        self.uploads: list[tuple] = []

    def put_json(self, bucket, key, obj):
        self.puts += 1
        if self.fail_put and self.puts > self.fail_after:
            raise RuntimeError("s3 down")
        self.objs[key] = dict(obj)
        self.history.append(obj["status"])

    def get_json(self, bucket, key):
        o = self.objs.get(key)
        return dict(o) if o else None

    def upload_file(self, bucket, key, src, content_type):
        self.uploads.append((bucket, key, Path(src).stat().st_size, content_type))

    def url(self, key):
        return f"https://cdn/{key}"

    def head_size(self, bucket, key):
        return self.sizes.get(key)

    def list_keys(self, bucket, prefix, limit):
        self.listings += 1
        found = [(k, 1) for k in self.keys if k.startswith(prefix)]
        if len(found) > limit:
            raise TooManyObjects("many")
        return found


def ok_fetch(storage, bucket, folder, tmp, max_bytes, max_files):
    root = Path(tmp) / "project"
    root.mkdir()
    return root


def ok_run(root, args, timeout_s, **kw):
    Path(args[args.index("--output") + 1] if "--output" in args else args[-1]).write_bytes(b"mp4data")
    return RunResult(0, "", False)


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(config, "S3_BUCKET", "b")
    monkeypatch.setattr(config, "SOURCE_BUCKET", "src")
    monkeypatch.setattr(config, "RENDER_QUEUE_MAX", 2)


def mgr(storage=None, fetch=ok_fetch, run=ok_run):
    storage = storage or FakeStorage(keys=["proj/index.html"], sizes={"z.zip": 5})
    return JobManager(storage, fetch=fetch, run=run), storage


def submit(m, folder="proj"):
    return m.submit(folder, "out", "draft", 30, "auto")


def test_happy_path_transitions_and_url():
    m, st = mgr()
    job = submit(m)
    assert job["status"] == "queued"
    m.run_pending()
    assert st.history == ["queued", "downloading", "rendering", "uploading", "done"]
    got = m.get(job["job_id"])
    assert got["status"] == "done"
    assert got["url"] == f"https://cdn/renders/proj/out-{job['job_id'][:8]}.mp4"
    assert got["render_s"] is not None and got["error"] is None
    assert st.uploads == [("b", f"renders/proj/out-{job['job_id'][:8]}.mp4", 7, "video/mp4")]


def test_zip_key_strips_extension():
    m, st = mgr()
    job = submit(m, "z.zip")
    m.run_pending()
    assert st.uploads[0][1] == f"renders/z/out-{job['job_id'][:8]}.mp4"


def test_nonzero_exit_fails_with_log_tail():
    m, _ = mgr(run=lambda *a, **k: RunResult(3, "boom tail", False))
    job = submit(m)
    m.run_pending()
    got = m.get(job["job_id"])
    assert got["status"] == "failed" and got["error"] == "hyperframes exited 3"
    assert got["log_tail"] == "boom tail"


def test_timeout_fails():
    m, _ = mgr(run=lambda *a, **k: RunResult(-15, "t", True))
    job = submit(m)
    m.run_pending()
    got = m.get(job["job_id"])
    assert got["status"] == "failed" and got["error"] == f"render timed out after {config.RENDER_TIMEOUT_S}s"


@pytest.mark.parametrize("content", [None, b""])
def test_missing_or_empty_output_fails(content):
    def run(root, args, timeout_s, **kw):
        if content is not None:
            Path(args[-1]).write_bytes(content)
        return RunResult(0, "", False)

    m, st = mgr(run=run)
    job = submit(m)
    m.run_pending()
    got = m.get(job["job_id"])
    assert got["status"] == "failed" and "output" in got["error"]
    assert st.uploads == []


@pytest.mark.parametrize("exc,msg", [
    (SourceNotFound("gone"), "gone"),
    (ProjectError("bad zip"), "bad zip"),
])
def test_source_errors_fail_with_message(exc, msg):
    def fetch(*a):
        raise exc

    m, _ = mgr(fetch=fetch)
    job = submit(m)
    m.run_pending()
    got = m.get(job["job_id"])
    assert got["status"] == "failed" and got["error"] == msg


@pytest.mark.parametrize("fail", [False, True])
def test_temp_dir_cleaned(fail):
    seen = []

    def fetch(storage, bucket, folder, tmp, mb, mf):
        seen.append(Path(tmp))
        return ok_fetch(storage, bucket, folder, tmp, mb, mf)

    m, _ = mgr(fetch=fetch, run=(lambda *a, **k: RunResult(1, "", False)) if fail else ok_run)
    submit(m)
    m.run_pending()
    assert seen and not seen[0].exists()


def test_queue_full():
    m, _ = mgr()
    submit(m)
    submit(m)
    with pytest.raises(QueueFull):
        submit(m)
    m.run_pending()
    submit(m)


def test_source_missing_before_queueing():
    m, st = mgr()
    with pytest.raises(SourceNotFound):
        submit(m, "nope")
    with pytest.raises(SourceNotFound):
        submit(m, "nope.zip")
    assert st.objs == {}


def test_prefix_exists_ignores_renders_and_handles_many():
    st = FakeStorage(keys=["only/renders/a.mp4"])
    m, _ = mgr(st)
    with pytest.raises(SourceNotFound):
        submit(m, "only")
    st2 = FakeStorage(keys=[f"big/f{i}" for i in range(config.RENDER_MAX_FILES + 5)])
    m2, _ = mgr(st2)
    assert submit(m2, "big")["status"] == "queued"


def test_stale_non_terminal_reported_interrupted():
    m, st = mgr()
    st.objs["jobs/abc.json"] = {"job_id": "abc", "status": "rendering", "error": None}
    got = m.get("abc")
    assert got["status"] == "failed" and got["error"] == "interrupted (service restarted)"
    st.objs["jobs/d.json"] = {"job_id": "d", "status": "done", "error": None}
    assert m.get("d")["status"] == "done"
    assert m.get("missing") is None


def test_queued_job_in_this_process_not_interrupted():
    m, _ = mgr()
    job = submit(m)
    assert m.get(job["job_id"])["status"] == "queued"


def test_worker_crash_marks_failed_and_continues():
    calls = []

    def run(root, args, timeout_s, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("kaboom")
        return ok_run(root, args, timeout_s)

    m, _ = mgr(run=run)
    a, b = submit(m), submit(m)
    m.run_pending()
    assert m.get(a["job_id"])["error"] == "internal error: RuntimeError"
    assert m.get(a["job_id"])["status"] == "failed"
    assert m.get(b["job_id"])["status"] == "done"


def test_put_json_failure_does_not_crash():
    m, st = mgr(FakeStorage(keys=["proj/index.html"], fail_put=True, fail_after=1))
    job = submit(m)
    m.run_pending()
    assert job["status"] == "queued"
    assert len(st.uploads) == 1  # later persists failed silently; job still ran


def test_first_put_failure_releases_slot_and_reraises():
    m, st = mgr(FakeStorage(keys=["proj/index.html"], fail_put=True))
    with pytest.raises(RuntimeError):
        submit(m)
    assert m._active == set() and m._q.empty()


def test_queue_full_checked_before_listing_s3():
    m, st = mgr()
    submit(m)
    submit(m)
    before = st.listings
    with pytest.raises(QueueFull):
        submit(m)
    assert st.listings == before


def test_uses_configured_render_prefix(monkeypatch):
    monkeypatch.setattr(config, "RENDER_PREFIX", "out")
    st = FakeStorage(keys=["only/out/a.mp4"])
    m, _ = mgr(st)
    with pytest.raises(SourceNotFound):
        submit(m, "only")
    st2 = FakeStorage(keys=["proj/index.html"])
    m2, _ = mgr(st2)
    job = submit(m2)
    m2.run_pending()
    assert st2.uploads[0][1].startswith("out/proj/")


def test_thread_runs_one_job_at_a_time():
    active, peak = [0], [0]
    lock = threading.Lock()

    def run(root, args, timeout_s, **kw):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return ok_run(root, args, timeout_s)

    m, _ = mgr(run=run)
    jobs = [submit(m), submit(m)]
    m.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and any(m.get(j["job_id"])["status"] != "done" for j in jobs):
            time.sleep(0.02)
    finally:
        m.stop()
    assert all(m.get(j["job_id"])["status"] == "done" for j in jobs)
    assert peak[0] == 1
