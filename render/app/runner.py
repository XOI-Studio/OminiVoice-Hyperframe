import logging
import os
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("render")

TAIL_BYTES = 4096
KILL_GRACE_S = 3


@dataclass
class RunResult:
    returncode: int
    log_tail: str
    timed_out: bool


ENV_KEEP = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
ENV_PREFIXES = ("HYPERFRAMES_", "PRODUCER_", "PUPPETEER_", "CHROME")


def _child_env() -> dict[str, str]:
    """Minimal env: the render runs project code, so no API key / AWS / S3 settings."""
    return {k: v for k, v in os.environ.items() if k in ENV_KEEP or k.startswith(ENV_PREFIXES)}


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:  # ProcessLookupError (already gone), PermissionError, ...
        pass


def run_render(
    root: Path,
    args: list[str],
    timeout_s: int,
    on_line: Callable[[str], None] | None = None,
    bin: str | None = None,
) -> RunResult:
    try:
        proc = subprocess.Popen(
            [bin or config.HYPERFRAMES_BIN, *args],
            cwd=root,
            env=_child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return RunResult(127, str(e), False)
    except OSError as e:
        return RunResult(126, str(e), False)

    chunks: deque[bytes] = deque()
    size = 0

    def read() -> None:
        nonlocal size
        assert proc.stdout is not None
        for line in proc.stdout:
            b = line.encode()
            chunks.append(b)
            size += len(b)
            while size - len(chunks[0]) >= TAIL_BYTES:
                size -= len(chunks.popleft())
            if on_line:
                try:
                    on_line(line.rstrip("\n"))
                except Exception:  # a bad callback must not stop us draining the pipe
                    log.exception("on_line callback failed")

    t = threading.Thread(target=read, daemon=True)
    t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
        _killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    else:
        _killpg(proc.pid, signal.SIGKILL)  # leave no stray Chrome/ffmpeg behind
    t.join(timeout=5)
    tail = b"".join(chunks)[-TAIL_BYTES:].decode(errors="ignore")
    rc = proc.returncode
    if timed_out and rc == 0:
        rc = -signal.SIGTERM
    return RunResult(rc, tail, timed_out)
