from pathlib import Path

import pytest

from app.render.validation import cli_args, validate_folder, validate_name


def test_rejects_empty_folder():
    for v in ("", "   "):
        with pytest.raises(ValueError):
            validate_folder(v)


@pytest.mark.parametrize("v", ["../a", "a/../b", "/abs/path", "a//b", "a\\b", "a/..", "a//"])
def test_rejects_dotdot_segments_and_leading_slash(v):
    with pytest.raises(ValueError):
        validate_folder(v)


@pytest.mark.parametrize("v", ["a;b", "a$b", "a*b", "é", "a" * 513])
def test_rejects_chars_outside_allowed_set(v):
    with pytest.raises(ValueError):
        validate_folder(v)


def test_accepts_nested_prefix_and_zip_key():
    assert validate_folder(" projects/my video-1/ ") == "projects/my video-1"
    assert validate_folder("a/b.c/clip.zip") == "a/b.c/clip.zip"


def test_rejects_name_longer_than_64_or_with_slash():
    assert validate_name("ok_name-1.mp4") == "ok_name-1.mp4"
    for v in ("a" * 65, "a/b", "", "abc\n"):
        with pytest.raises(ValueError):
            validate_name(v)


def test_maps_options_to_hyperframes_render_args():
    assert cli_args("draft", 30, "auto", Path("/tmp/o.mp4")) == [
        "render", "--quality", "draft", "--fps", "30", "--workers", "auto", "--output", "/tmp/o.mp4"
    ]
    assert cli_args("delivery", 60, "4", Path("o.mp4"))[6] == "4"
    assert cli_args("looks", 24, 8, Path("o.mp4"))[6] == "8"


def test_rejects_fps_25_quality_ultra_and_workers_99():
    with pytest.raises(ValueError):
        cli_args("draft", 25, "auto", Path("o"))
    with pytest.raises(ValueError):
        cli_args("ultra", 30, "auto", Path("o"))
    with pytest.raises(ValueError):
        cli_args("draft", 30, 99, Path("o"))


@pytest.mark.parametrize("w", ["abc", "0", 0, 9])
def test_rejects_invalid_workers(w):
    with pytest.raises(ValueError):
        cli_args("draft", 30, w, Path("o"))
