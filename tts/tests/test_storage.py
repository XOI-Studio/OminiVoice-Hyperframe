import pytest
from botocore.exceptions import ClientError

from app import config
from app.storage import IMMUTABLE, S3Storage


class FakeClient:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.put_kwargs = None

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def put_object(self, **kwargs):
        self.put_kwargs = kwargs
        self.objects[kwargs["Key"]] = kwargs["Body"]


@pytest.fixture(autouse=True)
def bucket(monkeypatch):
    monkeypatch.setattr(config, "S3_BUCKET", "bkt")
    monkeypatch.setattr(config, "S3_REGION", "r")
    monkeypatch.setattr(config, "S3_PUBLIC_BASE_URL", "")


def test_exists_is_true_only_for_stored_keys():
    s = S3Storage(client=FakeClient({"a.mp3": b"x"}))
    assert s.exists("a.mp3") is True
    assert s.exists("b.mp3") is False


def test_put_stores_with_content_type_and_immutable_cache_control():
    c = FakeClient()
    S3Storage(client=c).put("tts/v/a.mp3", b"data", "audio/mpeg")
    assert c.put_kwargs == {
        "Bucket": "bkt", "Key": "tts/v/a.mp3", "Body": b"data", "ContentType": "audio/mpeg", "CacheControl": IMMUTABLE,
    }


def test_url_percent_encodes_key():
    assert S3Storage(client=object()).url("tts/my voice/a #1.mp3") == "https://bkt.s3.r.amazonaws.com/tts/my%20voice/a%20%231.mp3"


def test_url_uses_public_base_url_when_set(monkeypatch):
    monkeypatch.setattr(config, "S3_PUBLIC_BASE_URL", "https://cdn.example.com")
    assert S3Storage(client=object()).url("tts/v/a.mp3") == "https://cdn.example.com/tts/v/a.mp3"
