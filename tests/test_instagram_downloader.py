"""InstagramDownloader на фикстурах и фейковом yt-dlp (решения 5–7, 9)."""

import asyncio
import hashlib
import logging
import threading
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError

from tests.fake_ytdlp import Script, load_fixture, probe_by_name
from tg_bot.downloader.instagram import FORMAT_SELECTOR, InstagramDownloader
from tg_bot.downloader.port import (
    DownloadFailed,
    LoginRequired,
    MediaKind,
    MediaTooLarge,
    NoMedia,
)
from tg_bot.downloader.ytdlp import (
    SEND_LIMIT_BYTES,
    SOCKET_TIMEOUT,
    Cancelled,
    ThreadRunner,
)
from tg_bot.links import Link, Platform, PostKind

REEL = load_fixture("instagram_reel")
PHOTO = load_fixture("instagram_photo_post")
MIXED = load_fixture("instagram_mixed_carousel")

REEL_LINK = Link(
    Platform.INSTAGRAM,
    PostKind.REEL,
    "DeRrI8_BTZz",
    "https://www.instagram.com/reel/DeRrI8_BTZz/",
)
P_LINK = Link(
    Platform.INSTAGRAM,
    PostKind.POST,
    "DeRrI8_BTZz",
    "https://www.instagram.com/p/DeRrI8_BTZz/",
)
PHOTO_LINK = Link(
    Platform.INSTAGRAM, PostKind.POST, "DdjcJv8Auv4", "https://www.instagram.com/p/DdjcJv8Auv4/"
)
MIXED_LINK = Link(
    Platform.INSTAGRAM, PostKind.POST, "DeEjFYODJXW", "https://www.instagram.com/p/DeEjFYODJXW/"
)

_EMPTY = "ERROR: [Instagram] X: Instagram sent an empty media response. Check if accessible"
_PARSE_JSON = (
    "ERROR: [Instagram] X: Failed to parse JSON (caused by JSONDecodeError()); please report"
)


def _cookie_line(name: str, value: str) -> str:
    return "\t".join([".instagram.com", "TRUE", "/", "TRUE", "1999999999", name, value])


def _cookies(*names: str) -> str:
    return "# Netscape HTTP Cookie File\n" + "\n".join(_cookie_line(n, "v") for n in names) + "\n"


def run_download(script: Script, link: Link, workdir: Path, *, cookies_file=None, probe=None):
    runner = ThreadRunner(limit=1, timeout=30, grace=30)
    try:
        downloader = InstagramDownloader(
            runner, cookies_file, ydl_factory=script.factory, probe=probe or probe_by_name()
        )
        return asyncio.run(downloader.download(link, workdir))
    finally:
        runner.close()


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


# --- Reel, фото, карусель ---------------------------------------------------------------------


def test_reel_video_item_with_ffprobe_metadata(workdir):
    script = Script(REEL)
    result = run_download(script, REEL_LINK, workdir)
    (item,) = result.items
    assert item.kind is MediaKind.VIDEO
    assert item.path.parent == workdir and item.path.exists()
    assert (item.width, item.height, item.duration) == (720, 1280, 73.3)  # из ffprobe, не из info
    assert result.caption == "Угорели по искусству? #наполеоны #рилс #сибиряки"


def test_metadata_comes_from_ffprobe_even_when_info_has_its_own(workdir):
    """С cookies в info есть width/height/duration (фикстура собрана вручную по notes) — всё
    равно берём ffprobe: без cookies там `None`, источник должен быть один."""
    manual = load_fixture("instagram_reel_with_cookies_manual")
    assert manual["duration"] == 73.3 and manual["formats"][0]["width"] == 720  # контроль входа
    probe = probe_by_name({"DeRrI8_BTZz.mp4": (721, 1281, 73.0)})
    (item,) = run_download(Script(manual), REEL_LINK, workdir, probe=probe).items
    assert (item.width, item.height, item.duration) == (721, 1281, 73.0)


def test_options_follow_decisions(workdir):
    script = Script(REEL)
    run_download(script, REEL_LINK, workdir)
    (options,) = script.factory_calls
    assert options["format"] == "b[format_id!*=dash]/bv*[vcodec^=avc1]+ba/bv*+ba/b"  # решение 5
    assert options["format"] == FORMAT_SELECTOR
    assert options["ignore_no_formats_error"] is True  # решение 6
    assert options["socket_timeout"] == SOCKET_TIMEOUT
    assert options["max_filesize"] == SEND_LIMIT_BYTES
    assert "cookiefile" not in options  # IG_COOKIES_FILE не задан
    assert script.extract_calls == [{"url": REEL_LINK.url, "download": False, "process": False}]
    assert script.process_calls == ["DeRrI8_BTZz"]


def test_p_link_with_single_video_behaves_like_reel(workdir, tmp_path):
    """`/p/<код>` не предполагает тип: ответ тот же — один видеоэлемент (решения 5, 14)."""
    other = tmp_path / "other"
    other.mkdir()
    via_reel = run_download(Script(REEL), REEL_LINK, workdir)
    via_p = run_download(Script(REEL), P_LINK, other)
    assert [i.kind for i in via_p.items] == [i.kind for i in via_reel.items] == [MediaKind.VIDEO]
    assert via_p.caption == via_reel.caption


def test_photo_post_downloads_unmarked_original(workdir):
    original = "<signed-url>?stp=dst-jpg_e35_tt6"
    script = Script(PHOTO, photos={original: b"JPEGDATA"})
    result = run_download(
        script, PHOTO_LINK, workdir, probe=probe_by_name({"01_photo.jpg": (3072, 4096, None)})
    )
    (item,) = result.items
    assert script.urls_opened == [original]
    assert script.process_calls == []  # фото не идёт через process_ie_result
    assert item.kind is MediaKind.PHOTO
    assert item.path == workdir / "01_photo.jpg"
    assert item.path.read_bytes() == b"JPEGDATA"
    assert (item.width, item.height, item.duration) == (3072, 4096, None)


def test_mixed_carousel_keeps_order_and_top_level_caption(workdir):
    script = Script(MIXED)
    result = run_download(script, MIXED_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.VIDEO]
    assert script.process_calls == ["DeEi5dYjHRK"]
    assert result.caption.startswith("Ловлю солнечные лучики")
    assert all(i.path.parent == workdir for i in result.items)


def test_unknown_entry_is_skipped_but_rest_is_delivered(workdir, caplog):
    info = load_fixture("instagram_mixed_carousel")
    info["entries"].insert(1, {"id": "weird"})
    with caplog.at_level(logging.WARNING):
        result = run_download(Script(info), MIXED_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.VIDEO]
    assert any("пропуск" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("info", [{"id": "X", "entries": []}, {"id": "X"}, None, "x"])
def test_nothing_usable_is_no_media(workdir, info):
    with pytest.raises(NoMedia):
        run_download(Script(info), REEL_LINK, workdir)


# --- фото: проверка размера картинки (решение 6) ---------------------------------------------


def _photo_run(workdir, size):
    return run_download(
        Script(PHOTO), PHOTO_LINK, workdir, probe=probe_by_name({"01_photo.jpg": (*size, None)})
    )


def test_small_photo_logs_warning_but_is_still_delivered(workdir, caplog):
    with caplog.at_level(logging.WARNING):
        result = _photo_run(workdir, (450, 800))
    assert len(result.items) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "450x800" in warnings[0].getMessage()


def test_original_size_photo_has_no_warning(workdir, caplog):
    """Положительный контроль к WARNING: тот же сценарий с оригиналом — записи нет."""
    with caplog.at_level(logging.WARNING):
        _photo_run(workdir, (3072, 4096))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_photo_with_exactly_min_short_side_has_no_warning(workdir, caplog):
    with caplog.at_level(logging.WARNING):
        _photo_run(workdir, (600, 900))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_photo_with_unknown_size_has_no_warning(workdir, caplog):
    with caplog.at_level(logging.WARNING):
        result = _photo_run(workdir, (None, None))
    assert result.items[0].width is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# --- ошибки ----------------------------------------------------------------------------------


def test_empty_media_response_is_login_required_without_cookies(workdir):
    with pytest.raises(LoginRequired):
        run_download(Script(error=YtDownloadError(_EMPTY)), REEL_LINK, workdir)


def test_photo_download_error_is_translated(workdir):
    script = Script(PHOTO, photo_error=YtDownloadError("HTTP Error 403"))
    with pytest.raises(DownloadFailed):
        run_download(script, PHOTO_LINK, workdir)


def test_video_skipped_by_max_filesize_is_too_large(workdir):
    script = Script(REEL, video_size=SEND_LIMIT_BYTES + 1)
    with pytest.raises(MediaTooLarge):
        run_download(script, REEL_LINK, workdir)


def test_video_over_limit_even_if_ytdlp_wrote_it_is_too_large(workdir):
    script = Script(REEL, video_size=SEND_LIMIT_BYTES + 1, ignore_max_filesize=True)
    with pytest.raises(MediaTooLarge):
        run_download(script, REEL_LINK, workdir)


def test_video_at_limit_is_accepted(workdir):
    result = run_download(Script(REEL, video_size=SEND_LIMIT_BYTES), REEL_LINK, workdir)
    assert result.items[0].path.stat().st_size == SEND_LIMIT_BYTES


def test_cancel_flag_is_not_translated(workdir):
    """`Cancelled` (флаг отмены) — не ошибка загрузки: наружу как есть, не `DownloadFailed`."""
    downloader = InstagramDownloader(
        ThreadRunner(limit=1), None, ydl_factory=Script(MIXED).factory, probe=probe_by_name()
    )
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        downloader._download_sync(MIXED_LINK, workdir, cancel)


# --- cookies (решение 7) ---------------------------------------------------------------------


@pytest.fixture
def cookies_source(tmp_path):
    path = tmp_path / "data" / "instagram.txt"
    path.parent.mkdir()
    path.write_text(_cookies("csrftoken", "sessionid", "mid", "datr"), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ytdlp_gets_private_copy_source_is_untouched(workdir, cookies_source):
    before = _sha(cookies_source)
    script = Script(REEL, cookies_after=_cookies("csrftoken", "sessionid", "mid"))  # datr потерян
    run_download(script, REEL_LINK, workdir, cookies_file=cookies_source)
    (options,) = script.factory_calls
    used = Path(options["cookiefile"])
    assert used != cookies_source and used.parent == workdir
    assert used.exists()  # живёт вместе с каталогом загрузки
    assert _sha(cookies_source) == before  # yt-dlp переписал копию, исходник цел


def test_copy_goes_away_with_workdir_even_after_error(tmp_path, cookies_source):
    import shutil

    workdir = tmp_path / "w2"
    workdir.mkdir()
    script = Script(error=YtDownloadError("ERROR: boom"))
    try:
        with pytest.raises(DownloadFailed):
            run_download(script, REEL_LINK, workdir, cookies_file=cookies_source)
        assert (workdir / "cookies.txt").exists()
    finally:
        shutil.rmtree(workdir)  # как pipeline: один finally
    assert not (workdir / "cookies.txt").exists()
    assert cookies_source.exists()


def test_parse_json_with_sessionid_removed_is_login_required(workdir, cookies_source):
    script = Script(
        error=YtDownloadError(_PARSE_JSON), cookies_after=_cookies("csrftoken", "mid")
    )  # yt-dlp удалил sessionid
    with pytest.raises(LoginRequired):
        run_download(script, REEL_LINK, workdir, cookies_file=cookies_source)


def test_parse_json_with_sessionid_kept_is_download_failed(workdir, cookies_source):
    """Положительный контроль: та же ошибка, `sessionid` на месте — не «cookies отвергнуты»."""
    script = Script(
        error=YtDownloadError(_PARSE_JSON), cookies_after=_cookies("csrftoken", "sessionid")
    )
    with pytest.raises(DownloadFailed):
        run_download(script, REEL_LINK, workdir, cookies_file=cookies_source)


def test_user_never_sees_ytdlp_text(workdir):
    with pytest.raises(DownloadFailed) as caught:
        run_download(Script(error=YtDownloadError(_PARSE_JSON)), REEL_LINK, workdir)
    assert "please report" not in caught.value.user_message


def test_nothing_is_written_outside_workdir(tmp_path, cookies_source):
    workdir = tmp_path / "isolated"
    workdir.mkdir()
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    run_download(Script(MIXED), MIXED_LINK, workdir, cookies_file=cookies_source)
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    assert new and all(workdir in p.parents for p in new)
