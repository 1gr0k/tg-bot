"""Аудит читателей внешних данных (notes плана, «Аудит читателей»): строки таблицы, у которых не
было собственного теста. Имена — по читателю: `test_reader_<что читаем>_<случай>`.

Ожидаемое поведение — пропуск с записью в лог (или понятная ошибка порта), а не падение и не
потеря остальных данных.
"""

import asyncio
import logging
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError

from tests.fake_ytdlp import FakeYDL, Script, load_fixture, probe_by_name
from tg_bot.downloader.instagram import InstagramDownloader, pick_photo_url
from tg_bot.downloader.port import DownloadFailed, MediaKind, NoMedia
from tg_bot.downloader.shorts import ShortsDownloader
from tg_bot.downloader.ytdlp import (
    ThreadRunner,
    cookie_names,
    parse_probe,
    translate_error,
)
from tg_bot.links import Link, Platform, PostKind

MIXED = load_fixture("instagram_mixed_carousel")
SHORTS = load_fixture("youtube_shorts_long")
IG_LINK = Link(Platform.INSTAGRAM, PostKind.POST, "DeEjFYODJXW", "https://www.instagram.com/p/X/")
YT_LINK = Link(Platform.YOUTUBE, PostKind.SHORTS, "ZoAPORyKG4A", "https://youtube.com/shorts/X")


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def _instagram(factory):
    runner = ThreadRunner(limit=1, timeout=30, grace=30)
    return InstagramDownloader(runner, ydl_factory=factory, probe=probe_by_name()), runner


def _download(downloader, runner, link, workdir):
    try:
        return asyncio.run(downloader.download(link, workdir))
    finally:
        runner.close()


class _ProcessReturns:
    """Фабрика: `process_ie_result` возвращает заданное вместо настоящего результата."""

    def __init__(self, script: Script, returned) -> None:
        self._script, self._returned = script, returned

    def __call__(self, options):
        returned = self._returned

        class Odd(FakeYDL):
            def process_ie_result(self, entry, download=True):
                self._script.process_calls.append(entry["id"])
                return returned

        return Odd(self._script, options)


# --- requested_downloads / результат process_ie_result ---------------------------------------


@pytest.mark.parametrize("returned", [{}, None, "x", {"requested_downloads": []}])
def test_reader_process_result_without_filepath_skips_video_keeps_photo(workdir, caplog, returned):
    script = Script(MIXED)
    downloader, runner = _instagram(_ProcessReturns(script, returned))
    with caplog.at_level(logging.WARNING):
        result = _download(downloader, runner, IG_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.PHOTO]  # видео пропущено, фото цело
    assert any("не вернул путь" in r.getMessage() for r in caplog.records)


def test_reader_process_result_without_filepath_for_only_video_is_no_media(workdir):
    reel = load_fixture("instagram_reel")
    downloader, runner = _instagram(_ProcessReturns(Script(reel), {}))
    with pytest.raises(NoMedia):
        _download(downloader, runner, IG_LINK, workdir)


def test_reader_requested_downloads_missing_in_shorts_is_download_failed_not_too_large(workdir):
    """Нет пути файла — это не «слишком большой»: ошибка загрузки, а не три повтора бюджета."""
    script = Script(SHORTS, attempt_sizes=[1_000])

    class NoPath(FakeYDL):
        def extract_info(self, url, download=True, process=True):
            info = super().extract_info(url, download, process)
            info.pop("requested_downloads", None)
            return info

    runner = ThreadRunner(limit=1, timeout=30, grace=30)
    downloader = ShortsDownloader(
        runner, ydl_factory=lambda o: NoPath(script, o), probe=probe_by_name()
    )
    with pytest.raises(DownloadFailed):
        _download(downloader, runner, YT_LINK, workdir)
    assert len(script.extract_calls) == 1  # повторов нет


# --- thumbnails -------------------------------------------------------------------------------


@pytest.mark.parametrize("thumbnails", [[{"url": None}], [{}], [None], ["x"], [{"url": 5}]])
def test_reader_thumbnails_without_usable_url_skips_photo_with_log(workdir, caplog, thumbnails):
    info = load_fixture("instagram_mixed_carousel")
    info["entries"][0]["thumbnails"] = thumbnails  # непустой список → «фото», но URL нет
    downloader, runner = _instagram(Script(info).factory)
    with caplog.at_level(logging.WARNING):
        result = _download(downloader, runner, IG_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.VIDEO]
    assert any("нет пригодного URL" in r.getMessage() for r in caplog.records)


def test_reader_thumbnail_stp_huge_digits_do_not_crash():
    url = f"<signed-url>?stp=dst-jpg_p{'9' * 5000}x{'9' * 5000}_tt6"
    assert pick_photo_url([{"url": url}]) == url  # маркером не считается → «без маркера»


# --- ошибки yt-dlp ----------------------------------------------------------------------------


def test_reader_error_chain_with_cycle_does_not_hang():
    first, second = Exception("a"), Exception("b")
    first.cause, second.cause = second, first  # type: ignore[attr-defined]
    assert type(translate_error(first)) is DownloadFailed


@pytest.mark.parametrize("status", ["429", None, 4.29, [429]])
def test_reader_error_status_not_an_int_is_not_rate_limit(status):
    class Cause(Exception):
        pass

    cause = Cause("x")
    cause.status = status  # type: ignore[attr-defined]
    error = YtDownloadError("ERROR: net", exc_info=(Cause, cause, None))
    assert type(translate_error(error)) is DownloadFailed


@pytest.mark.parametrize("message", ["", "   ", "\n"])
def test_reader_error_text_empty_is_download_failed(message):
    assert type(translate_error(YtDownloadError(message))) is DownloadFailed


# --- ffprobe ----------------------------------------------------------------------------------


@pytest.mark.parametrize("streams", [None, 5, "x", {"a": 1}, [None, 5, "x"]])
def test_reader_ffprobe_streams_odd_type_gives_empty_probe(streams):
    probe = parse_probe({"streams": streams, "format": {"duration": "3.5"}})
    assert (probe.width, probe.height, probe.duration) == (None, None, 3.5)


@pytest.mark.parametrize("side_data", [5, "x", {"rotation": 90}, [None, "x"], [{"rotation": "90"}]])
def test_reader_ffprobe_side_data_odd_type_is_ignored(side_data):
    data = {
        "streams": [{"codec_type": "video", "width": 4, "height": 3, "side_data_list": side_data}]
    }
    probe = parse_probe(data)
    assert (probe.width, probe.height) == (4, 3)  # поворот не применён, ничего не упало


def test_reader_ffprobe_stream_without_codec_type_is_not_video():
    assert parse_probe({"streams": [{"width": 4, "height": 3}]}).width is None


# --- файл cookies -----------------------------------------------------------------------------


def test_reader_cookies_file_odd_lines_are_ignored(tmp_path):
    good = "\t".join([".instagram.com", "TRUE", "/", "TRUE", "1", "csrftoken", "v"])
    path = tmp_path / "c.txt"
    path.write_bytes(
        "\n".join(["", "# comment", "   ", "short\tline", "a\tb\tc", good, "\x00\x01"]).encode()
        + b"\n\xff\xfe\xfd\n"
    )
    assert cookie_names(path) == {"csrftoken"}


def test_reader_cookies_file_empty_gives_no_names(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    assert cookie_names(path) == set()


def test_reader_cookies_path_is_directory_gives_no_names(tmp_path):
    assert cookie_names(Path(tmp_path)) == set()
