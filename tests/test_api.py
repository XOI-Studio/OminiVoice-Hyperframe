import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app, get_engine, get_storage
from app.text import split_text


class FakeEngine:
    def voices(self):
        return {"narrator_male": {"category": "narrator", "channel": "ChuyenMa"}, "sumuc": {"category": "narrator", "channel": "SuMuc"}, "ghost": {"category": "special", "channel": "ChuyenMa"}}

    def synthesize(self, text, voice, language, steps, seed, wpm):
        return np.zeros(24000, np.float32), 24000


class FakeStorage:
    def __init__(self):
        self.objs = {}

    def exists(self, key):
        return key in self.objs

    def put(self, key, data, content_type):
        self.objs[key] = (data, content_type)

    def url(self, key):
        return f"https://bucket.s3.test/{key}"


@pytest.fixture
def client():
    storage = FakeStorage()
    app.dependency_overrides[get_engine] = lambda: FakeEngine()
    app.dependency_overrides[get_storage] = lambda: storage
    c = TestClient(app)
    c.storage = storage
    yield c
    app.dependency_overrides.clear()


def test_tts_returns_s3_url_then_cache(client):
    body = {"text": "Xin chào các bạn.", "voice": "sumuc"}
    r = client.post("/tts", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["url"].startswith("https://bucket.s3.test/") and j["url"].endswith(".wav")
    assert j["duration_s"] == 1.0 and j["cached"] is False
    assert client.post("/tts", json=body).json()["cached"] is True
    assert len(client.storage.objs) == 1


def test_unknown_voice_404(client):
    assert client.post("/tts", json={"text": "hi", "voice": "nope"}).status_code == 404


def test_empty_text_422(client):
    assert client.post("/tts", json={"text": ""}).status_code == 422
    assert client.post("/tts", json={"text": "   "}).status_code == 422


def test_too_long_413(client):
    assert client.post("/tts", json={"text": "a" * (config.MAX_TEXT_CHARS + 1)}).status_code == 413


def test_api_key(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert client.post("/tts", json={"text": "hi"}).status_code == 401
    ok = client.post("/tts", json={"text": "hi"}, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


def test_voices_and_health(client):
    assert set(client.get("/voices").json()) == {"narrator_male", "sumuc", "ghost"}
    assert set(client.get("/voices?category=special").json()) == {"ghost"}
    assert set(client.get("/voices?channel=SuMuc").json()) == {"sumuc"}
    assert client.get("/health").json() == {"status": "ok"}


def test_split_text():
    assert split_text("Một. Hai! Ba?", 100) == ["Một. Hai! Ba?"]
    assert split_text("aaaa. bbbb. cccc.", 11) == ["aaaa. bbbb.", "cccc."]
    long = " ".join(["từ"] * 50)
    assert all(len(c) <= 40 for c in split_text(long, 40))
    assert split_text("   ", 10) == []
