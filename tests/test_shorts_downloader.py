"""ShortsDownloader на фикстуре и фейковом yt-dlp (решения 5, 9)."""

import asyncio
import logging
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError

from tests.fake_ytdlp import Script, load_fixture, probe_by_name
from tg_bot.downloader.port import DownloadFailed, MediaKind, MediaNotFound, MediaTooLarge
from tg_bot.downloader.shorts import FORMAT_SORT, ShortsDownloader, caption_of
from tg_bot.downloader.ytdlp import BUDGETS, SEND_LIMIT_BYTES, ThreadRunner
from tg_bot.links import Link, Platform, PostKind

SHORTS = load_fixture("youtube_shorts_long")
LINK = Link(
    Platform.YOUTUBE,
    PostKind.SHORTS,
    "ZoAPORyKG4A",
    "https://www.youtube.com/shorts/ZoAPORyKG4A",
)


def run_download(script: Script, workdir: Path, probe=None):
    runner = ThreadRunner(limit=1, timeout=30, grace=30)
    try:
        downloader = ShortsDownloader(
            runner, ydl_factory=script.factory, probe=probe or probe_by_name()
        )
        return asyncio.run(downloader.download(LINK, workdir))
    finally:
        runner.close()


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def test_one_video_item_with_metadata_and_caption(workdir):
    probe = probe_by_name({"ZoAPORyKG4A.mp4": (720, 1280, 135.0)})
    result = run_download(Script(SHORTS, attempt_sizes=[19_000_000]), workdir, probe)
    (item,) = result.items
    assert item.kind is MediaKind.VIDEO
    assert item.path == workdir / "attempt-1" / "ZoAPORyKG4A.mp4"
    assert (item.width, item.height, item.duration) == (720, 1280, 135.0)  # из ffprobe
    assert result.caption.startswith("Продолжение к обзору")


def test_options_follow_decisions(workdir):
    script = Script(SHORTS, attempt_sizes=[1_000_000])
    run_download(script, workdir)
    (options,) = script.factory_calls
    assert options["format"] == BUDGETS[0].selector
    assert options["format_sort"] == ["vcodec:h264", "acodec:aac", "res:1080", "ext:mp4:m4a"]
    assert FORMAT_SORT == options["format_sort"]
    assert options["merge_output_format"] == "mp4"
    assert options["noplaylist"] is True
    assert options["socket_timeout"] == 20
    assert "cookiefile" not in options  # Shorts без cookies
    assert script.extract_calls == [{"url": LINK.url, "download": True, "process": True}]


def test_fact_over_limit_retries_with_next_budget_level(workdir):
    """135 с в 1080p — 58 440 000 байт (проба 5): следующий уровень, новый каталог в workdir."""
    script = Script(SHORTS, attempt_sizes=[58_440_000, 19_180_000])
    result = run_download(script, workdir)
    assert [o["format"] for o in script.factory_calls] == [BUDGETS[0].selector, BUDGETS[1].selector]
    (item,) = result.items
    assert item.path.stat().st_size == 19_180_000
    assert item.path.parent == workdir / "attempt-2"
    assert not (workdir / "attempt-1").exists()  # большой файл не лежит до конца загрузки


def test_all_levels_over_limit_is_too_large(workdir):
    script = Script(SHORTS, attempt_sizes=[SEND_LIMIT_BYTES + 1] * 3, ignore_max_filesize=True)
    with pytest.raises(MediaTooLarge):
        run_download(script, workdir)
    assert len(script.factory_calls) == len(BUDGETS)


def test_file_skipped_by_max_filesize_counts_as_too_large(workdir):
    script = Script(SHORTS, attempt_sizes=[SEND_LIMIT_BYTES + 1, 2_000_000])
    result = run_download(script, workdir)
    assert len(script.factory_calls) == 2
    assert result.items[0].path.stat().st_size == 2_000_000


def test_missing_file_without_size_message_is_download_failed_not_retry(workdir):
    """Точка 2: «файла нет» без сообщения yt-dlp о max_filesize — сбой, а не «слишком большой»:
    следующий уровень бюджета не пробуется."""
    script = Script(SHORTS, attempt_sizes=[SEND_LIMIT_BYTES + 1], silent_skip=True)
    with pytest.raises(DownloadFailed) as caught:
        run_download(script, workdir)
    assert not isinstance(caught.value, MediaTooLarge)
    assert len(script.factory_calls) == 1


def test_size_message_is_logged_with_numbers(workdir, caplog):
    script = Script(SHORTS, attempt_sizes=[SEND_LIMIT_BYTES + 1, 1_000_000])
    with caplog.at_level(logging.INFO):
        run_download(script, workdir)
    assert str(SEND_LIMIT_BYTES + 1) in caplog.text


def test_unavailable_video_is_not_found(workdir):
    script = Script(
        error=YtDownloadError("ERROR: [youtube] xxxxxxxxxxx: This video is unavailable")
    )
    with pytest.raises(MediaNotFound):
        run_download(script, workdir)


def test_writes_only_inside_workdir(tmp_path, workdir):
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    run_download(Script(SHORTS, attempt_sizes=[SEND_LIMIT_BYTES + 1, 1_000_000]), workdir)
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    assert new and all(workdir in p.parents for p in new)


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"description": "описание", "title": "название"}, "описание"),
        ({"description": "", "title": "название"}, "название"),
        ({"description": None, "title": "название"}, "название"),
        ({"description": "  \n", "title": "название"}, "название"),
        ({"title": "название"}, "название"),
        ({"description": "", "title": ""}, ""),
        ({"description": 5, "title": ["x"]}, ""),
        ({}, ""),
        (None, ""),
        ("x", ""),
    ],
)
def test_caption_description_then_title_then_empty(info, expected):
    assert caption_of(info) == expected
