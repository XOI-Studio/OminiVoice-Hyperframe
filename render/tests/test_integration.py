"""Real render: fixture project -> JobManager (real fetch + real hyperframes CLI) -> mp4. Run with HF_INTEGRATION=1."""
import json
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from app import config
from app.jobs import JobManager

pytestmark = pytest.mark.skipif(os.environ.get("HF_INTEGRATION") != "1", reason="set HF_INTEGRATION=1 to run real renders")

FIXTURE = Path(__file__).parent / "fixtures" / "mini-project"


class DirStorage:
    """S3 stand-in backed by directories: <root>/<bucket>/<key>."""

    def __init__(self, root: Path):
        self.root = root

    def _p(self, bucket, key) -> Path:
        return self.root / bucket / key

    def list_keys(self, bucket, prefix, limit):
        base = self.root / bucket
        keys = [(str(p.relative_to(base)), p.stat().st_size) for p in base.rglob("*") if p.is_file()]
        return [(k, s) for k, s in sorted(keys) if k.startswith(prefix)]

    def head_size(self, bucket, key):
        p = self._p(bucket, key)
        return p.stat().st_size if p.is_file() else None

    def download_file(self, bucket, key, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._p(bucket, key), dest)

    def upload_file(self, bucket, key, src: Path, content_type):
        p = self._p(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)

    def put_json(self, bucket, key, obj):
        p = self._p(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj))

    def get_json(self, bucket, key):
        p = self._p(bucket, key)
        return json.loads(p.read_text()) if p.is_file() else None

    def url(self, key):
        return f"file://{self.root}/out/{key}"


@pytest.fixture
def hf_bin(tmp_path, monkeypatch):
    if shutil.which("hyperframes"):
        monkeypatch.setattr(config, "HYPERFRAMES_BIN", "hyperframes")
        return
    wrapper = tmp_path / "hyperframes"  # local dev: no global install, go through npx at the pinned version
    wrapper.write_text('#!/bin/sh\nexec npx --yes hyperframes@0.8.58 "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(config, "HYPERFRAMES_BIN", str(wrapper))


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_BUCKET", "src")
    monkeypatch.setattr(config, "S3_BUCKET", "out")
    monkeypatch.setenv("HYPERFRAMES_NO_TELEMETRY", "1")
    return DirStorage(tmp_path / "s3")


def render(storage, folder: str) -> dict:
    jobs = JobManager(storage)
    job = jobs.submit(folder, "ep01", "draft", 30, 2)
    jobs.run_pending()
    return jobs.get(job["job_id"])


def assert_good_mp4(storage, job: dict):
    assert job["status"] == "done", job
    key = job["url"].split("/out/", 1)[1]
    mp4 = storage.root / "out" / key
    assert mp4.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.2
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}


def test_renders_a_prefix_project_to_a_playable_mp4(storage, hf_bin):
    shutil.copytree(FIXTURE, storage.root / "src" / "projects" / "ep01")
    job = render(storage, "projects/ep01")
    assert job["url"].endswith(".mp4") and "/renders/projects/ep01/ep01-" in job["url"]
    assert_good_mp4(storage, job)


def test_renders_a_zip_project_with_a_single_top_folder(storage, hf_bin, tmp_path):
    zpath = storage.root / "src" / "zips" / "ep01.zip"
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FIXTURE.rglob("*"):
            if f.is_file():
                z.write(f, Path("mini-project") / f.relative_to(FIXTURE))
    assert_good_mp4(storage, render(storage, "zips/ep01.zip"))


def test_a_project_without_index_html_fails_with_a_reason(storage, hf_bin):
    (storage.root / "src" / "bad").mkdir(parents=True)
    (storage.root / "src" / "bad" / "readme.txt").write_text("not a project")
    job = render(storage, "bad")
    assert job["status"] == "failed" and "index.html" in job["error"]
