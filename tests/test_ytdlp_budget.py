"""Бюджет размера (решение 9), опции yt-dlp (решение 8) и метаданные из ffprobe (решение 5)."""

import json
import logging
import subprocess
import threading
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.ffmpeg_gate import require_ffmpeg
from tg_bot.downloader.port import MediaTooLarge
from tg_bot.downloader.ytdlp import (
    BUDGETS,
    SEND_LIMIT_BYTES,
    SOCKET_TIMEOUT,
    Budget,
    Cancelled,
    MediaProbe,
    YdlLogger,
    base_options,
    ensure_fits,
    fit_budget,
    parse_probe,
    probe_media,
    progress_hook,
)

# --- селектор бюджета -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "selector"),
    [
        (
            0,
            "bv*[filesize<45000000]+ba/bv*[filesize_approx<40000000]+ba/"
            "b[filesize<48000000]/b[filesize_approx<43000000]/bv*+ba/b",
        ),
        (
            1,
            "bv*[filesize<25000000]+ba/bv*[filesize_approx<22000000]+ba/"
            "b[filesize<28000000]/b[filesize_approx<25000000]/bv*+ba/b",
        ),
        (
            2,
            "bv*[filesize<12000000]+ba/bv*[filesize_approx<10000000]+ba/"
            "b[filesize<14000000]/b[filesize_approx<12000000]/bv*+ba/b",
        ),
    ],
)
def test_budget_selector_table(level, selector):
    assert BUDGETS[level].selector == selector


def test_budget_levels_are_decreasing_and_in_bytes():
    assert len(BUDGETS) == 3
    for upper, lower in pairwise(BUDGETS):
        assert upper.video > lower.video and upper.total > lower.total
    assert all(b.total < SEND_LIMIT_BYTES for b in BUDGETS)  # запас под merge


# --- fit_budget: факт после загрузки ---------------------------------------------------------


def _sparse(path: Path, size: int) -> Path:
    with path.open("wb") as fh:
        fh.truncate(size)
    return path


class Attempts:
    """Фейковая загрузка: на каждом уровне отдаёт файл заданного размера (None — пропуск)."""

    def __init__(self, sizes: list[int | None]) -> None:
        self.sizes = sizes
        self.calls: list[tuple[Budget, Path]] = []

    def __call__(self, budget: Budget, subdir: Path) -> Path | None:
        self.calls.append((budget, subdir))
        size = self.sizes[len(self.calls) - 1]
        return None if size is None else _sparse(subdir / "v.mp4", size)


def _run(sizes, tmp_path, cancel=None):
    attempts = Attempts(sizes)
    path = fit_budget(attempts, tmp_path, cancel or threading.Event())
    return attempts, path


def test_format_without_filesize_within_limit_is_sent(tmp_path):
    """Выбран вариант без размера (у Instagram `filesize` нет), факт в лимите — берём."""
    attempts, path = _run([20_000_000], tmp_path)
    assert path.stat().st_size == 20_000_000
    assert [b for b, _ in attempts.calls] == [BUDGETS[0]]


def test_format_without_filesize_over_limit_goes_to_next_level(tmp_path):
    attempts, path = _run([SEND_LIMIT_BYTES + 1, 10_000_000], tmp_path)
    assert path.stat().st_size == 10_000_000
    assert [b for b, _ in attempts.calls] == [BUDGETS[0], BUDGETS[1]]


def test_fact_larger_than_promised_goes_to_next_level(tmp_path):
    """`filesize` обещал 40 000 000, файл вышел 55 000 000 → следующий уровень, а не отправка."""
    attempts, path = _run([55_000_000, 30_000_000], tmp_path)
    assert path.stat().st_size == 30_000_000
    assert len(attempts.calls) == 2


def test_oversize_attempt_is_removed_before_next_one(tmp_path):
    attempts, path = _run([55_000_000, 1_000_000], tmp_path)
    first_dir, second_dir = attempts.calls[0][1], attempts.calls[1][1]
    assert first_dir != second_dir
    assert not first_dir.exists()  # диск освобождён
    assert path.parent == second_dir
    assert second_dir.parent == tmp_path  # новый каталог внутри workdir


def test_all_levels_over_limit_raises_media_too_large(tmp_path):
    big = SEND_LIMIT_BYTES + 5
    attempts = Attempts([big, big, big])
    with pytest.raises(MediaTooLarge):
        fit_budget(attempts, tmp_path, threading.Event())
    assert len(attempts.calls) == len(BUDGETS)


def test_exact_limit_passes_one_byte_more_does_not(tmp_path):
    exact_dir, over_dir = tmp_path / "exact", tmp_path / "over"
    exact_dir.mkdir()
    over_dir.mkdir()
    attempts, path = _run([SEND_LIMIT_BYTES], exact_dir)
    assert path.stat().st_size == SEND_LIMIT_BYTES
    assert len(attempts.calls) == 1
    attempts, _ = _run([SEND_LIMIT_BYTES + 1, 1], over_dir)
    assert len(attempts.calls) == 2


def test_ytdlp_skipped_file_by_max_filesize_counts_as_too_large(tmp_path):
    """Проба: `max_filesize` — без исключения и без файла; «файла нет» = слишком большой."""
    attempts, path = _run([None, 5_000_000], tmp_path)
    assert path.stat().st_size == 5_000_000
    assert len(attempts.calls) == 2


def test_cancel_flag_stops_before_next_attempt(tmp_path):
    cancel = threading.Event()
    cancel.set()
    attempts = Attempts([1])
    with pytest.raises(Cancelled):
        fit_budget(attempts, tmp_path, cancel)
    assert attempts.calls == []


def test_ensure_fits_boundary(tmp_path):
    ok = _sparse(tmp_path / "ok.mp4", SEND_LIMIT_BYTES)
    assert ensure_fits(ok) == ok
    big = _sparse(tmp_path / "big.mp4", SEND_LIMIT_BYTES + 1)
    with pytest.raises(MediaTooLarge):
        ensure_fits(big)


# --- опции и хук -----------------------------------------------------------------------------


def test_progress_hook_raises_only_when_flag_set():
    cancel = threading.Event()
    hook = progress_hook(cancel)
    hook({"status": "downloading"})  # флага нет — не бросает
    cancel.set()
    with pytest.raises(Cancelled):
        hook({"status": "downloading"})


def test_base_options_have_socket_timeout_hook_and_size_guard():
    cancel = threading.Event()
    options = base_options(cancel)
    assert options["socket_timeout"] == SOCKET_TIMEOUT == 20
    assert options["max_filesize"] == SEND_LIMIT_BYTES
    assert "cookiefile" not in options
    cancel.set()
    with pytest.raises(Cancelled):
        options["progress_hooks"][0]({})  # хук связан именно с этим флагом


_SKIP_MSG = "\r[download] File is larger than max-filesize (200000 bytes > 100000 bytes). Aborting."


def test_logger_flags_max_filesize_message_once_and_logs_sizes(caplog):
    """Точка 2: сообщение yt-dlp (проба) ставит флаг на попытку; размер — в лог."""
    logger = YdlLogger()
    assert logger.take_size_exceeded() is False
    with caplog.at_level(logging.INFO, logger="tg_bot.downloader.ytdlp"):
        logger.debug(_SKIP_MSG)
    assert "200000" in caplog.text and "100000" in caplog.text
    assert logger.take_size_exceeded() is True
    assert logger.take_size_exceeded() is False  # флаг на одну попытку: считан — сброшен


@pytest.mark.parametrize(
    "message",
    [
        "[download] Destination: /tmp/x.mp4",
        "[info] x: Downloading 1 format(s): mp4",
        "[download] Download completed",
        "File is larger than something else",
        "",
    ],
)
def test_logger_ignores_other_debug_messages(message):
    logger = YdlLogger()
    logger.debug(message)
    assert logger.take_size_exceeded() is False


def test_logger_warning_and_error_do_not_set_flag(caplog):
    logger = YdlLogger()
    logger.warning(_SKIP_MSG)
    logger.error(_SKIP_MSG)
    assert logger.take_size_exceeded() is False


def test_base_options_use_given_logger():
    logger = YdlLogger()
    assert base_options(threading.Event(), logger=logger)["logger"] is logger
    assert isinstance(base_options(threading.Event())["logger"], YdlLogger)


def test_base_options_cookiefile_only_with_copy(tmp_path):
    copy = SimpleNamespace(path=tmp_path / "cookies.txt")
    assert base_options(threading.Event(), cookies=copy)["cookiefile"] == str(copy.path)  # type: ignore[arg-type]


# --- ffprobe ---------------------------------------------------------------------------------

_PROBE = {
    "streams": [
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "video", "width": 720, "height": 1280, "duration": "73.3"},
    ],
    "format": {"duration": "73.366"},
}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_PROBE, MediaProbe(720, 1280, 73.366)),
        (  # длительность формата нет — берём у потока
            {"streams": [{"codec_type": "video", "width": 4, "height": 3, "duration": "2.5"}]},
            MediaProbe(4, 3, 2.5),
        ),
        (  # поворот 90° меняет стороны местами
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "side_data_list": [{"rotation": -90}],
                    }
                ]
            },
            MediaProbe(1080, 1920, None),
        ),
        (  # картинка: длительности нет
            {"streams": [{"codec_type": "video", "width": 3072, "height": 4096}], "format": {}},
            MediaProbe(3072, 4096, None),
        ),
        (
            {"streams": [{"codec_type": "video", "width": "x", "height": 0}]},
            MediaProbe(None, None, None),
        ),
        (
            {"streams": [{"codec_type": "video", "width": True, "height": 5}]},
            MediaProbe(None, 5, None),
        ),
        (
            {"streams": [{"codec_type": "audio"}], "format": {"duration": "N/A"}},
            MediaProbe(None, None, None),
        ),
        ({"streams": None, "format": None}, MediaProbe(None, None, None)),
        ({}, MediaProbe(None, None, None)),
        ([], MediaProbe(None, None, None)),
        (None, MediaProbe(None, None, None)),
    ],
)
def test_parse_probe(data, expected):
    assert parse_probe(data) == expected


def _fake_run(returncode=0, stdout=b"", raises=None):
    def run(command, **kwargs):
        assert command[0] == "ffprobe"
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    return run


def test_probe_media_reads_json(tmp_path):
    run = _fake_run(stdout=json.dumps(_PROBE).encode())
    assert probe_media(tmp_path / "a.mp4", run=run) == MediaProbe(720, 1280, 73.366)


@pytest.mark.parametrize(
    "run",
    [
        _fake_run(returncode=1),
        _fake_run(stdout=b"not json"),
        _fake_run(raises=FileNotFoundError("ffprobe")),
        _fake_run(raises=subprocess.TimeoutExpired("ffprobe", 20)),
    ],
)
def test_probe_media_failure_gives_empty_probe(tmp_path, run):
    assert probe_media(tmp_path / "a.mp4", run=run) == MediaProbe(None, None, None)


def test_probe_media_real_ffprobe(tmp_path):
    require_ffmpeg()  # нет ffmpeg: skip, а при REQUIRE_FFMPEG=1 (CI) — fail
    video = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48:d=0.5", str(video)],
        check=True,
    )
    probe = probe_media(video)
    assert (probe.width, probe.height) == (64, 48)
    assert probe.duration == pytest.approx(0.5, abs=0.2)
