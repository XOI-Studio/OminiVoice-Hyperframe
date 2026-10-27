import os
import stat
import time
from pathlib import Path

from app.runner import run_render


def script(tmp_path: Path, body: str) -> str:
    p = tmp_path / "fake.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_returns_0_and_log_tail_on_success(tmp_path):
    b = script(tmp_path, "echo hello\necho world\n")
    lines: list[str] = []
    r = run_render(tmp_path, [], 10, on_line=lines.append, bin=b)
    assert r.returncode == 0 and not r.timed_out
    assert "hello" in r.log_tail and "world" in r.log_tail
    assert [x.strip() for x in lines] == ["hello", "world"]


def test_returns_nonzero_code_and_error_text(tmp_path):
    b = script(tmp_path, "echo boom >&2\nexit 3\n")
    r = run_render(tmp_path, [], 10, bin=b)
    assert r.returncode == 3 and "boom" in r.log_tail and not r.timed_out


def test_kills_process_group_and_flags_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    b = script(tmp_path, f"sleep 60 &\necho $! > {pidfile}\nwait\n")
    t0 = time.time()
    r = run_render(tmp_path, [], 1, bin=b)
    assert r.timed_out and r.returncode != 0
    assert time.time() - t0 < 15
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("child still alive")


def test_keeps_only_last_4096_bytes(tmp_path):
    b = script(tmp_path, "i=0\nwhile [ $i -lt 2000 ]; do echo line-$i-xxxxxxxxxx; i=$((i+1)); done\n")
    r = run_render(tmp_path, [], 10, bin=b)
    assert len(r.log_tail.encode()) <= 4096
    assert "line-1999-" in r.log_tail and "line-0-" not in r.log_tail


def test_runs_in_project_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    b = script(tmp_path, "pwd\necho \"$@\"\n")
    r = run_render(root, ["render", "--fps", "30"], 10, bin=b)
    assert str(root.resolve()) in r.log_tail
    assert "render --fps 30" in r.log_tail


def test_missing_binary_returns_127(tmp_path):
    r = run_render(tmp_path, [], 10, bin=str(tmp_path / "nope"))
    assert r.returncode == 127 and r.log_tail and not r.timed_out


def test_on_line_exception_does_not_hang_the_run(tmp_path):
    script = tmp_path / "noisy.sh"
    script.write_text("#!/bin/sh\nfor i in $(seq 1 40000); do echo line $i; done\n")
    script.chmod(0o755)

    def boom(_line):
        raise RuntimeError("callback failed")

    res = run_render(tmp_path, [], timeout_s=10, on_line=boom, bin=str(script))
    assert res.returncode == 0 and not res.timed_out
    assert "line 40000" in res.log_tail


def test_non_executable_binary_returns_126(tmp_path):
    script = tmp_path / "noexec.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    res = run_render(tmp_path, [], timeout_s=5, bin=str(script))
    assert res.returncode == 126


def test_child_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "k-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("S3_BUCKET", "bkt")
    monkeypatch.setenv("HYPERFRAMES_BROWSER_PATH", "/chrome")
    b = script(tmp_path, 'echo "k=$API_KEY aws=$AWS_SECRET_ACCESS_KEY s3=$S3_BUCKET hf=$HYPERFRAMES_BROWSER_PATH path=$PATH home=$HOME"\n')
    r = run_render(tmp_path, [], 10, bin=b)
    assert "k= " in r.log_tail and "aws= " in r.log_tail and "s3= " in r.log_tail
    assert "secret" not in r.log_tail
    assert "hf=/chrome" in r.log_tail
    assert f"path={os.environ['PATH']}" in r.log_tail and f"home={os.environ['HOME']}" in r.log_tail
