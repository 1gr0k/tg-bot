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
    RateLimited,
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
    assert result.skipped == 1  # неопознанный элемент — тоже не доставленный
    assert any("пропуск" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("info", [{"id": "X", "entries": []}, {"id": "X"}, None, "x"])
def test_nothing_usable_is_no_media(workdir, info):
    with pytest.raises(NoMedia):
        run_download(Script(info), REEL_LINK, workdir)


# --- элементы карусели: пропуск неудавшихся и сверх лимита (M2) -------------------------------


def _carousel(*videos: str) -> dict:
    """Фото, затем видео с заданными id (по образцу видео-элемента смешанной карусели)."""
    info = load_fixture("instagram_mixed_carousel")
    photo, video = info["entries"]
    info["entries"] = [photo]
    for video_id in videos:
        info["entries"].append(video | {"id": video_id})
    return info


OVER = SEND_LIMIT_BYTES + 1


def test_one_of_three_over_limit_is_skipped_with_counter(workdir, caplog):
    script = Script(
        _carousel("VIDEO_OK", "VIDEO_BIG"), video_size={"VIDEO_OK": 1_000, "VIDEO_BIG": OVER}
    )
    with caplog.at_level(logging.WARNING):
        result = run_download(script, MIXED_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.VIDEO]
    assert [i.path.name for i in result.items] == ["01_photo.jpg", "VIDEO_OK.mp4"]
    assert result.skipped == 1
    assert script.process_calls == ["VIDEO_OK", "VIDEO_BIG"]  # после пропуска идём дальше
    assert any("элемент 3 из 3 пропущен" in r.getMessage() for r in caplog.records)


def test_over_limit_file_written_by_ytdlp_is_removed(workdir):
    script = Script(
        _carousel("VIDEO_OK", "VIDEO_BIG"),
        video_size={"VIDEO_OK": 1_000, "VIDEO_BIG": OVER},
        ignore_max_filesize=True,
    )
    result = run_download(script, MIXED_LINK, workdir)
    assert result.skipped == 1
    assert not (workdir / "VIDEO_BIG.mp4").exists()  # большой файл не ждёт очистки каталога


def test_all_over_limit_is_too_large(workdir):
    script = Script(_carousel("A", "B"), video_size=OVER)
    script.info["entries"].pop(0)  # только два видео
    with pytest.raises(MediaTooLarge):
        run_download(script, MIXED_LINK, workdir)
    assert script.process_calls == ["A", "B"]  # попробованы оба, а не только первый


def test_nothing_succeeded_raises_error_of_first_element(workdir):
    """Фото упало (`DownloadFailed`), видео сверх лимита (`MediaTooLarge`) — наружу первая."""
    script = Script(
        _carousel("BIG"), video_size=OVER, photo_error=YtDownloadError("HTTP Error 403")
    )
    with pytest.raises(DownloadFailed):
        run_download(script, MIXED_LINK, workdir)


def test_failed_photo_is_skipped_video_is_delivered(workdir):
    script = Script(_carousel("OK"), photo_error=YtDownloadError("HTTP Error 403"))
    result = run_download(script, MIXED_LINK, workdir)
    assert [i.kind for i in result.items] == [MediaKind.VIDEO]
    assert result.skipped == 1


def test_clean_carousel_has_zero_skipped(workdir):
    assert run_download(Script(MIXED), MIXED_LINK, workdir).skipped == 0


def test_cancel_during_carousel_is_not_swallowed_as_skip(workdir):
    """`Cancelled` — не ошибка элемента: пропуском не становится."""
    downloader = InstagramDownloader(
        ThreadRunner(limit=1),
        None,
        ydl_factory=Script(_carousel("A")).factory,
        probe=probe_by_name(),
    )
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        downloader._download_sync(MIXED_LINK, workdir, cancel)


# --- Instagram без повтора по бюджету (M3) ----------------------------------------------------


def test_oversize_video_is_not_retried_with_lower_budget(workdir):
    """Решение 9: H.264 у Instagram только прогрессивный, ниже — DASH VP9 (отвергнут решением 5):
    сверх лимита — сразу `MediaTooLarge`, одна загрузка, один набор опций."""
    script = Script(REEL, video_size=OVER)
    with pytest.raises(MediaTooLarge):
        run_download(script, REEL_LINK, workdir)
    assert script.process_calls == ["DeRrI8_BTZz"]
    assert len(script.factory_calls) == 1


# --- «файла нет»: слишком большой или сбой yt-dlp (точка 2) -----------------------------------


def test_missing_file_with_size_message_is_too_large_and_size_is_logged(workdir, caplog):
    script = Script(REEL, video_size=OVER)
    with caplog.at_level(logging.INFO), pytest.raises(MediaTooLarge):
        run_download(script, REEL_LINK, workdir)
    assert str(OVER) in caplog.text  # размер из сообщения yt-dlp — в лог


def test_missing_file_without_size_message_is_download_failed(workdir):
    script = Script(REEL, video_size=OVER, silent_skip=True)
    with pytest.raises(DownloadFailed) as caught:
        run_download(script, REEL_LINK, workdir)
    assert not isinstance(caught.value, MediaTooLarge)


def test_size_message_of_previous_element_does_not_leak_to_next(workdir, caplog):
    """Флаг — на элемент: сообщение первого (большого) не делает «слишком большим» второй, у
    которого файла нет, а yt-dlp молчит."""
    info = _carousel("BIG", "GONE")
    info["entries"].pop(0)  # только два видео
    script = Script(info, video_size=OVER)
    original = script.size_for

    def quiet_second(entry_id: str) -> int:
        script.silent_skip = entry_id == "GONE"  # у второго yt-dlp молчит
        return original(entry_id)

    script.size_for = quiet_second  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING), pytest.raises(MediaTooLarge):  # первая ошибка — у BIG
        run_download(script, MIXED_LINK, workdir)
    messages = [r.getMessage() for r in caplog.records]
    assert any("элемент 1 из 2 пропущен: MediaTooLarge" in m for m in messages)
    assert any("элемент 2 из 2 пропущен: DownloadFailed" in m for m in messages)


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


# --- сигнал владельцу `owner_alert` (решение 7, «изменено по ревью части B (2)») ---------------


class _TooManyRequests(YtDownloadError):
    status = 429


def _videos(*ids: str) -> dict:
    """Карусель только из видео с заданными id."""
    info = _carousel(*ids)
    info["entries"].pop(0)  # без фото
    return info


SESSION_LOST = _cookies("csrftoken", "mid")  # yt-dlp при `close()` убрал `sessionid`
SESSION_KEPT = _cookies("csrftoken", "sessionid", "mid")
_PARSE_FAIL = YtDownloadError(_PARSE_JSON)


def test_session_lost_and_no_element_succeeded_is_login_required(workdir, cookies_source):
    """Проверка `session_lost()` — после выхода из `with ydl`: до `close()` файл ещё цел."""
    script = Script(
        _videos("A", "B"),
        entry_errors={"A": _PARSE_FAIL, "B": _PARSE_FAIL},
        cookies_after=SESSION_LOST,
    )
    with pytest.raises(LoginRequired):
        run_download(script, MIXED_LINK, workdir, cookies_file=cookies_source)
    assert script.process_calls == ["A", "B"]  # оба элемента попробованы


def test_session_kept_and_no_element_succeeded_is_download_failed(workdir, cookies_source):
    """Положительный контроль к предыдущему: `sessionid` на месте — прежняя причина первого."""
    script = Script(
        _videos("A", "B"),
        entry_errors={"A": _PARSE_FAIL, "B": _PARSE_FAIL},
        cookies_after=SESSION_KEPT,
    )
    with pytest.raises(DownloadFailed):
        run_download(script, MIXED_LINK, workdir, cookies_file=cookies_source)


def test_session_lost_with_one_of_two_gives_owner_alert(workdir, cookies_source):
    script = Script(_videos("A", "B"), entry_errors={"A": _PARSE_FAIL}, cookies_after=SESSION_LOST)
    result = run_download(script, MIXED_LINK, workdir, cookies_file=cookies_source)
    assert [i.path.name for i in result.items] == ["B.mp4"]
    assert result.skipped == 1
    assert result.owner_alert is LoginRequired


def test_session_lost_without_failed_elements_still_gives_owner_alert(workdir, cookies_source):
    script = Script(MIXED, cookies_after=SESSION_LOST)
    result = run_download(script, MIXED_LINK, workdir, cookies_file=cookies_source)
    assert result.skipped == 0 and result.owner_alert is LoginRequired


def test_session_kept_gives_no_owner_alert_even_with_failed_element(workdir, cookies_source):
    """Положительный контроль: та же ошибка элемента, `sessionid` на месте — сигнала нет."""
    script = Script(_videos("A", "B"), entry_errors={"A": _PARSE_FAIL}, cookies_after=SESSION_KEPT)
    result = run_download(script, MIXED_LINK, workdir, cookies_file=cookies_source)
    assert result.skipped == 1 and result.owner_alert is None


def test_clean_download_has_no_owner_alert(workdir, cookies_source):
    for script, cookies_file in [
        (Script(MIXED), None),
        (Script(MIXED, cookies_after=SESSION_KEPT), cookies_source),
    ]:
        assert (
            run_download(script, MIXED_LINK, workdir, cookies_file=cookies_file).owner_alert is None
        )


def test_rate_limited_element_gives_owner_alert_when_others_succeed(workdir):
    script = Script(_videos("A", "B"), entry_errors={"A": _TooManyRequests("HTTP Error 429")})
    result = run_download(script, MIXED_LINK, workdir)
    assert [i.path.name for i in result.items] == ["B.mp4"]
    assert result.skipped == 1 and result.owner_alert is RateLimited


def test_login_required_element_gives_owner_alert_when_others_succeed(workdir):
    script = Script(_videos("A", "B"), entry_errors={"A": YtDownloadError(_EMPTY)})
    result = run_download(script, MIXED_LINK, workdir)
    assert result.skipped == 1 and result.owner_alert is LoginRequired


def test_other_element_errors_give_no_owner_alert(workdir):
    script = Script(
        _carousel("OK", "BIG"), video_size={"OK": 1_000, "BIG": OVER}, photo_error=_PARSE_FAIL
    )
    result = run_download(script, MIXED_LINK, workdir)
    assert result.skipped == 2 and result.owner_alert is None  # `DownloadFailed`, `MediaTooLarge`


# --- ошибка элемента: одна запись в лог, неожиданная форма данных — пропуск (L) ---------------


def test_failed_element_is_logged_once(workdir, caplog):
    script = Script(_videos("A", "B"), entry_errors={"A": YtDownloadError("ERROR: boom")})
    with caplog.at_level(logging.DEBUG):
        run_download(script, MIXED_LINK, workdir)
    about_a = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(about_a) == 1 and "элемент 1 из 2 пропущен" in about_a[0].getMessage()


def test_single_failed_reel_is_logged_once(workdir, caplog):
    script = Script(REEL, entry_errors={"DeRrI8_BTZz": YtDownloadError("ERROR: boom")})
    with caplog.at_level(logging.DEBUG), pytest.raises(DownloadFailed):
        run_download(script, REEL_LINK, workdir)
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.parametrize(
    "error",
    [KeyError("formats"), TypeError("NoneType"), ValueError("bad int")],
    ids=["KeyError", "TypeError", "ValueError"],
)
def test_unexpected_data_error_on_one_element_is_a_skip_not_an_abort(workdir, caplog, error):
    script = Script(_videos("A", "B"), entry_errors={"A": error})
    with caplog.at_level(logging.WARNING):
        result = run_download(script, MIXED_LINK, workdir)
    assert [i.path.name for i in result.items] == ["B.mp4"]
    assert result.skipped == 1 and result.owner_alert is None
    assert any("элемент 1 из 2 пропущен" in r.getMessage() for r in caplog.records)


def test_unexpected_data_error_on_all_elements_is_download_failed(workdir):
    script = Script(_videos("A"), entry_errors={"A": TypeError("NoneType")})
    with pytest.raises(DownloadFailed):
        run_download(script, MIXED_LINK, workdir)
