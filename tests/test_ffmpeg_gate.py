"""`require_ffmpeg`: пропуск локально, падение при `REQUIRE_FFMPEG=1` (CI не зеленит без ffmpeg)."""

import pytest
from _pytest.outcomes import Failed, Skipped

from tests.ffmpeg_gate import require_ffmpeg


def outcome() -> str:
    """Исход вызова: `Skipped` не должен «просочиться» наружу и выдать тест за пропущенный."""
    try:
        require_ffmpeg()
    except Failed:
        return "fail"
    except Skipped:
        return "skip"
    return "pass"


def install(tmp_path, *names: str) -> None:
    for name in names:
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)


@pytest.fixture
def path_dir(monkeypatch, tmp_path):
    """Единственный каталог в PATH — пустой: ни ffmpeg, ни ffprobe, пока тест их не положит."""
    monkeypatch.setenv("PATH", str(tmp_path))
    return tmp_path


def test_missing_ffmpeg_skips_by_default(path_dir, monkeypatch):
    monkeypatch.delenv("REQUIRE_FFMPEG", raising=False)
    assert outcome() == "skip"


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_exact_one_requires_ffmpeg(path_dir, monkeypatch, value):
    monkeypatch.setenv("REQUIRE_FFMPEG", value)
    assert outcome() == "skip"


def test_missing_ffmpeg_fails_when_required(path_dir, monkeypatch):
    monkeypatch.setenv("REQUIRE_FFMPEG", "1")
    assert outcome() == "fail"


@pytest.mark.parametrize("present", ["ffmpeg", "ffprobe"])
def test_only_one_of_two_tools_is_not_enough(path_dir, monkeypatch, present):
    install(path_dir, present)
    monkeypatch.setenv("REQUIRE_FFMPEG", "1")
    assert outcome() == "fail"


@pytest.mark.parametrize("required", ["1", ""])
def test_present_tools_pass_in_any_mode(path_dir, monkeypatch, required):
    """Положительный контроль: оба инструмента есть — ни skip, ни fail."""
    install(path_dir, "ffmpeg", "ffprobe")
    monkeypatch.setenv("REQUIRE_FFMPEG", required)
    assert outcome() == "pass"
