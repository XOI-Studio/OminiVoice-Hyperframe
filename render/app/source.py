import stat
import zipfile
from pathlib import PurePosixPath
from pathlib import Path

from . import config
from .storage import TooManyObjects


class SourceNotFound(Exception):
    pass


class ProjectError(Exception):
    pass


def _copy_capped(src, dst, budget: int) -> int:
    total = 0
    while chunk := src.read(1 << 20):
        total += len(chunk)
        if total > budget:
            raise ProjectError("zip uncompressed size exceeds limit")
        dst.write(chunk)
    return total


def _is_junk(name: str) -> bool:
    """Finder zip noise: the __MACOSX/ tree and AppleDouble (._*) files."""
    parts = [x for x in name.replace("\\", "/").split("/") if x]
    return bool(parts) and (parts[0] == "__MACOSX" or any(x.startswith("._") for x in parts))


def _safe_extract(zip_path: Path, out: Path, max_bytes: int, max_files: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    root = out.resolve()
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise ProjectError(f"invalid zip: {e}") from e
    with z:
        infos = [i for i in z.infolist() if not _is_junk(i.filename)]
        if len(infos) > max_files:
            raise ProjectError(f"zip has more than {max_files} entries")
        if sum(i.file_size for i in infos) > max_bytes:
            raise ProjectError(f"zip uncompressed size exceeds {max_bytes} bytes")
        for i in infos:
            p = PurePosixPath(i.filename)
            if p.is_absolute() or i.filename.startswith(("/", "\\")) or ".." in i.filename.replace("\\", "/").split("/"):
                raise ProjectError(f"unsafe path in zip: {i.filename!r}")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ProjectError(f"symlink in zip: {i.filename!r}")
        total = 0
        for i in infos:
            if i.is_dir():
                continue
            target = (out / i.filename).resolve()
            if not target.is_relative_to(root):
                raise ProjectError(f"unsafe path in zip: {i.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as dst:
                total += _copy_capped(src, dst, max_bytes - total)


def _find_root(project: Path) -> Path:
    if (project / "index.html").is_file():
        return project
    dirs = [c for c in project.iterdir() if c.is_dir() and c.name != "__MACOSX"] if project.is_dir() else []
    if len(dirs) == 1 and (dirs[0] / "index.html").is_file():
        return dirs[0]
    raise ProjectError("index.html not found")


def fetch_project(storage, bucket: str, folder: str, dest: Path, max_bytes: int, max_files: int) -> Path:
    project = dest / "project"
    if folder.endswith(".zip"):
        size = storage.head_size(bucket, folder)
        if size is None:
            raise SourceNotFound(folder)
        if size > max_bytes:
            raise ProjectError(f"zip exceeds {max_bytes} bytes")
        zip_path = dest / "_src.zip"
        storage.download_file(bucket, folder, zip_path)
        try:
            _safe_extract(zip_path, project, max_bytes, max_files)
        finally:
            zip_path.unlink(missing_ok=True)
    else:
        prefix = folder.rstrip("/") + "/"
        try:
            keys = storage.list_keys(bucket, prefix, max_files)
        except TooManyObjects as e:
            raise ProjectError(str(e)) from e
        out_prefix = prefix + config.RENDER_PREFIX.strip("/") + "/"
        keys = [(k, s) for k, s in keys if not k.startswith(out_prefix)]
        if not keys:
            raise SourceNotFound(folder)
        if sum(s for _, s in keys) > max_bytes:
            raise ProjectError(f"project exceeds {max_bytes} bytes")
        for k, _ in keys:
            if k.endswith("/"):
                continue
            rel = k[len(prefix):]
            if rel.startswith("/") or "\\" in rel or any(seg in ("", "..") for seg in rel.split("/")):
                raise ProjectError(f"unsafe key: {k!r}")
            target = (project / rel).resolve()
            if not target.is_relative_to(project.resolve()):
                raise ProjectError(f"unsafe key: {k!r}")
            storage.download_file(bucket, k, target)
    return _find_root(project)
