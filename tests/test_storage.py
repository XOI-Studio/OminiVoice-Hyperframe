import json

import pytest
from botocore.exceptions import ClientError

from app.storage import S3Storage, TooManyObjects


def _err(code: str, op: str = "GetObject") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.kwargs = None

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.pages)


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeClient:
    def __init__(self, pages=None, objects=None):
        self.paginator = FakePaginator(pages or [])
        self.objects = objects or {}
        self.uploads = []
        self.puts = []
        self.downloads = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _err("NoSuchKey")
        return {"Body": FakeBody(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _err("404", "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.uploads.append((Filename, Bucket, Key, ExtraArgs))

    def download_file(self, Bucket, Key, Filename):
        self.downloads.append((Bucket, Key, Filename))

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def _page(*items):
    return {"Contents": [{"Key": k, "Size": s} for k, s in items]}


def test_lists_all_pages_of_a_prefix():
    c = FakeClient(pages=[_page(("p/a", 1), ("p/b", 2)), {}, _page(("p/c", 3))])
    assert S3Storage(client=c).list_keys("bkt", "p/", 10) == [("p/a", 1), ("p/b", 2), ("p/c", 3)]
    assert c.paginator.kwargs == {"Bucket": "bkt", "Prefix": "p/"}


def test_raises_when_more_than_limit_objects():
    c = FakeClient(pages=[_page(("a", 1), ("b", 1)), _page(("c", 1))])
    with pytest.raises(TooManyObjects):
        S3Storage(client=c).list_keys("bkt", "", 2)
    assert len(S3Storage(client=FakeClient(pages=[_page(("a", 1), ("b", 1))])).list_keys("bkt", "", 2)) == 2


def test_get_json_returns_none_on_no_such_key():
    s = S3Storage(client=FakeClient(objects={"k": b'{"a": 1}'}))
    assert s.get_json("bkt", "k") == {"a": 1}
    assert s.get_json("bkt", "missing") is None


def test_get_json_reraises_other_errors():
    class Boom(FakeClient):
        def get_object(self, Bucket, Key):
            raise _err("AccessDenied")

    with pytest.raises(ClientError):
        S3Storage(client=Boom()).get_json("bkt", "k")


def test_put_json_is_not_cached():
    c = FakeClient()
    S3Storage(client=c).put_json("bkt", "k", {"a": 1})
    p = c.puts[0]
    assert p["Bucket"] == "bkt" and p["Key"] == "k"
    assert json.loads(p["Body"]) == {"a": 1}
    assert p["ContentType"] == "application/json" and p["CacheControl"] == "no-store"


def test_upload_file_sets_content_type_and_cache_control(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    c = FakeClient()
    S3Storage(client=c).upload_file("bkt", "k.mp4", f, "video/mp4")
    assert c.uploads == [(str(f), "bkt", "k.mp4", {
        "ContentType": "video/mp4", "CacheControl": "public, max-age=31536000, immutable",
    })]


def test_download_file_creates_parent_dirs(tmp_path):
    c = FakeClient()
    dest = tmp_path / "a" / "b" / "f.txt"
    S3Storage(client=c).download_file("bkt", "k", dest)
    assert dest.parent.is_dir()
    assert c.downloads == [("bkt", "k", str(dest))]


def test_head_size_returns_none_when_missing():
    s = S3Storage(client=FakeClient(objects={"k": b"abc"}))
    assert s.head_size("bkt", "k") == 3
    assert s.head_size("bkt", "missing") is None


def test_url_percent_encodes_key(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "S3_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "S3_BUCKET", "bkt")
    monkeypatch.setattr(config, "S3_REGION", "r")
    u = S3Storage(client=object()).url("renders/my proj/out #1.mp4")
    assert u == "https://bkt.s3.r.amazonaws.com/renders/my%20proj/out%20%231.mp4"
