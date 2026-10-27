import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app, get_jobs
from app.render.jobs import QueueFull
from app.render.source import SourceNotFound

JID = "a" * 32


class FakeJobs:
    def __init__(self):
        self.submitted = []
        self.error = None
        self.jobs = {JID: {"job_id": JID, "status": "done", "url": "https://x/out.mp4"}}

    def submit(self, folder, name, quality, fps, workers):
        if self.error:
            raise self.error
        self.submitted.append((folder, name, quality, fps, workers))
        return {"job_id": JID, "status": "queued"}

    def get(self, job_id):
        return self.jobs.get(job_id)


@pytest.fixture
def jobs():
    j = FakeJobs()
    app.dependency_overrides[get_jobs] = lambda: j
    yield j
    app.dependency_overrides.clear()


@pytest.fixture
def client(jobs):
    return TestClient(app)


def test_post_render_returns_202_and_status_url(client, jobs):
    r = client.post("/render", json={"folder": "proj/a"})
    assert r.status_code == 202
    assert r.json() == {"job_id": JID, "status": "queued", "status_url": f"/jobs/{JID}"}
    assert jobs.submitted == [("proj/a", "out", "looks", 30, "auto")]


@pytest.mark.parametrize("body", [
    {"folder": "../etc"}, {"folder": ""}, {"folder": "a", "quality": "ultra"},
    {"folder": "a", "fps": 25}, {"folder": "a", "name": "a/b"}, {"folder": "a", "workers": 99},
    {"folder": "a", "workers": "x"}, {"folder": "a", "format": "gif"},
])
def test_rejects_bad_input_with_422(client, jobs, body):
    assert client.post("/render", json=body).status_code == 422
    assert jobs.submitted == []


def test_404_when_source_missing(client, jobs):
    jobs.error = SourceNotFound("a")
    r = client.post("/render", json={"folder": "a"})
    assert r.status_code == 404 and r.json()["detail"] == "source not found"


def test_429_when_queue_full(client, jobs):
    jobs.error = QueueFull()
    r = client.post("/render", json={"folder": "a"})
    assert r.status_code == 429 and r.json()["detail"] == "render queue full"


def test_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "secret")
    assert client.post("/render", json={"folder": "a"}).status_code == 401
    assert client.get(f"/jobs/{JID}").status_code == 401
    h = {"X-API-Key": "secret"}
    assert client.post("/render", json={"folder": "a"}, headers=h).status_code == 202
    assert client.get(f"/jobs/{JID}", headers=h).status_code == 200


def test_get_job(client):
    r = client.get(f"/jobs/{JID}")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert client.get(f"/jobs/{'b' * 32}").status_code == 404


@pytest.mark.parametrize("jid", ["abc", "../x", "A" * 32, "g" * 32, "a" * 33])
def test_get_job_non_hex_id_404(client, jid):
    assert client.get(f"/jobs/{jid}").status_code == 404


def test_503_when_render_not_configured(monkeypatch):
    monkeypatch.setattr(app.state, "jobs", None)
    r = TestClient(app).post("/render", json={"folder": "a"})
    assert r.status_code == 503 and r.json()["detail"] == "render not configured"
    assert TestClient(app).get(f"/jobs/{JID}").status_code == 503


def test_503_when_storage_unavailable(client, jobs):
    jobs.error = RuntimeError("s3 down")
    r = client.post("/render", json={"folder": "a"})
    assert r.status_code == 503 and r.json()["detail"] == "storage unavailable"
