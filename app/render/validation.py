import re
from pathlib import Path

QUALITIES = ("draft", "looks", "delivery")
FPS = (24, 30, 60)
MAX_WORKERS = 8

_FOLDER_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def validate_folder(folder: str) -> str:
    f = folder.strip()
    if "//" in f or "\\" in f:
        raise ValueError("invalid folder")
    if f.endswith("/"):
        f = f[:-1]
    if not f or len(f) > 512 or f.startswith("/"):
        raise ValueError("invalid folder")
    if ".." in f.split("/") or not _FOLDER_RE.fullmatch(f):
        raise ValueError("invalid folder")
    return f


def validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError("invalid name")
    return name


def cli_args(quality: str, fps: int, workers: str | int, output: Path) -> list[str]:
    if quality not in QUALITIES:
        raise ValueError("invalid quality")
    if fps not in FPS:
        raise ValueError("invalid fps")
    if workers != "auto":
        try:
            w = int(workers)
        except (TypeError, ValueError):
            raise ValueError("invalid workers") from None
        if not 1 <= w <= MAX_WORKERS:
            raise ValueError("invalid workers")
        workers = w
    return ["render", "--quality", quality, "--fps", str(fps), "--workers", str(workers), "--output", str(output)]
