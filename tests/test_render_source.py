import io
import zipfile
from pathlib import Path

import pytest

from app.render.source import ProjectError, _copy_capped, SourceNotFound, fetch_project
from app.storage import TooManyObjects


class FakeStorage:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def list_keys(self, bucket, prefix, limit):
        keys = [(k, len(v)) for k, v in sorted(self.objects.items()) if k.startswith(prefix)]
        if len(keys) > limit:
            raise TooManyObjects("too many")
        return keys

    def download_file(self, bucket, key, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[key])

    def head_size(self, bucket, key):
        return len(self.objects[key]) if key in self.objects else None


def make_zip(entries: dict[str, bytes], symlinks: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def fetch(objs, folder, tmp_path, max_bytes=10_000, max_files=50):
    return fetch_project(FakeStorage(objs), "b", folder, tmp_path, max_bytes, max_files)


def test_prefix_download_skips_renders(tmp_path):
    objs = {
        "p/proj/index.html": b"<html>",
        "p/proj/css/a.css": b"a{}",
        "p/proj/renders/old.mp4": b"x",
        "p/other/x.txt": b"x",
    }
    root = fetch(objs, "p/proj/", tmp_path)
    assert root == tmp_path / "project"
    assert (root / "index.html").read_bytes() == b"<html>"
    assert (root / "css" / "a.css").exists()
    assert not (root / "renders").exists()


def test_zip_extracts_single_subfolder(tmp_path):
    z = make_zip({"site/index.html": b"<html>", "site/a.js": b"1", "site/": b""})
    root = fetch({"p/site.zip": z}, "p/site.zip", tmp_path)
    assert root == tmp_path / "project" / "site"
    assert (root / "a.js").exists()
    assert not (tmp_path / "_src.zip").exists()


def test_zip_root_index(tmp_path):
    root = fetch({"s.zip": make_zip({"index.html": b"x"})}, "s.zip", tmp_path)
    assert root == tmp_path / "project"


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/abs.txt", "..\\evil.txt"])
def test_zip_slip_rejected(tmp_path, name):
    z = make_zip({"index.html": b"x", name: b"bad"})
    with pytest.raises(ProjectError):
        fetch({"s.zip": z}, "s.zip", tmp_path)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_zip_symlink_rejected(tmp_path):
    z = make_zip({"index.html": b"x"}, symlinks=("link",))
    with pytest.raises(ProjectError):
        fetch({"s.zip": z}, "s.zip", tmp_path)


def test_zip_bomb_rejected(tmp_path):
    z = make_zip({"index.html": b"x", "big.bin": b"\0" * 50_000})
    assert len(z) < 10_000
    with pytest.raises(ProjectError):
        fetch({"s.zip": z}, "s.zip", tmp_path)


def test_zip_too_large_compressed(tmp_path):
    with pytest.raises(ProjectError):
        fetch({"s.zip": b"x" * 200}, "s.zip", tmp_path, max_bytes=100)


def test_zip_too_many_files(tmp_path):
    z = make_zip({"index.html": b"x", **{f"f{i}": b"x" for i in range(10)}})
    with pytest.raises(ProjectError):
        fetch({"s.zip": z}, "s.zip", tmp_path, max_files=5)


def test_prefix_too_many_objects(tmp_path):
    objs = {f"p/f{i}": b"x" for i in range(10)}
    with pytest.raises(ProjectError):
        fetch(objs, "p", tmp_path, max_files=5)


def test_prefix_too_large(tmp_path):
    with pytest.raises(ProjectError):
        fetch({"p/index.html": b"x" * 200}, "p", tmp_path, max_bytes=100)


def test_not_found(tmp_path):
    with pytest.raises(SourceNotFound):
        fetch({}, "p/", tmp_path)
    with pytest.raises(SourceNotFound):
        fetch({"p/renders/a.mp4": b"x"}, "p/", tmp_path)
    with pytest.raises(SourceNotFound):
        fetch({}, "missing.zip", tmp_path)


def test_no_index_html(tmp_path):
    with pytest.raises(ProjectError, match="index.html not found"):
        fetch({"p/a.txt": b"x"}, "p/", tmp_path)
    with pytest.raises(ProjectError, match="index.html not found"):
        fetch({"p/a/index.html": b"x", "p/b/index.html": b"x"}, "p/", tmp_path)


@pytest.mark.parametrize("key", ["p/proj/../../x", "p/proj/a//b", "p/proj//abs", "p/proj/a\\b", "p/proj/a/../../../x"])
def test_prefix_unsafe_key_rejected(tmp_path, key):
    dest = tmp_path / "d"
    dest.mkdir()
    with pytest.raises(ProjectError):
        fetch({"p/proj/index.html": b"x", key: b"bad"}, "p/proj/", dest)
    assert not (tmp_path / "x").exists()


def test_renders_sibling_kept(tmp_path):
    objs = {"proj/index.html": b"x", "proj/renders-old/x": b"1", "proj/renders/x": b"2"}
    root = fetch(objs, "proj", tmp_path)
    assert (root / "renders-old" / "x").exists()
    assert not (root / "renders").exists()


def test_copy_capped_streaming_limit():
    out = io.BytesIO()
    with pytest.raises(ProjectError):
        _copy_capped(io.BytesIO(b"\0" * 5_000_000), out, 1_000_000)
    assert _copy_capped(io.BytesIO(b"abc"), io.BytesIO(), 3) == 3


def test_finder_zip_ignores_macosx_and_appledouble(tmp_path):
    z = make_zip({
        "project/index.html": b"<html>",
        "project/._index.html": b"\x00\x05",
        "__MACOSX/project/._index.html": b"\x00\x05",
        "__MACOSX/._project": b"\x00",
    })
    root = fetch({"p.zip": z}, "p.zip", tmp_path)
    assert root.name == "project" and (root / "index.html").read_bytes() == b"<html>"
    assert not (tmp_path / "project" / "__MACOSX").exists()
    assert not (root / "._index.html").exists()


def test_prefix_download_uses_configured_render_prefix(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "RENDER_PREFIX", "out")
    objs = {"proj/index.html": b"x", "proj/out/a.mp4": b"2", "proj/renders/b.txt": b"3"}
    root = fetch(objs, "proj", tmp_path)
    assert (root / "renders" / "b.txt").exists() and not (root / "out").exists()
