"""Перевод ошибок yt-dlp → ошибки порта и копия cookies (решение 7)."""

import hashlib
import stat
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError
from yt_dlp.utils import ExtractorError

from tg_bot.downloader.port import (
    DownloadFailed,
    LoginRequired,
    MediaNotFound,
    RateLimited,
)
from tg_bot.downloader.ytdlp import (
    SESSION_COOKIE,
    Cancelled,
    cookie_names,
    copy_cookies,
    describe,
    translate_error,
    translating_errors,
)

_EMPTY = (
    "ERROR: [Instagram] BkfuX9UB-eK: Instagram sent an empty media response. Check if this post "
    "is accessible in your browser without being logged-in."
)
_PARSE_JSON = (
    "ERROR: [Instagram] DeRrI8_BTZz: Failed to parse JSON (caused by JSONDecodeError("
    "\"Expecting value in '': line 1 column 1 (char 0)\")); please report this issue on "
    "https://github.com/yt-dlp/yt-dlp/issues?q= ."
)
_UNAVAILABLE = "ERROR: [youtube] xxxxxxxxxxx: This video is unavailable"


class _HttpCause(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP Error {status}")
        self.status = status


def _with_cause(message: str, cause: BaseException) -> YtDownloadError:
    return YtDownloadError(message, exc_info=(type(cause), cause, None))


@pytest.mark.parametrize(
    ("exc", "session_lost", "expected"),
    [
        (YtDownloadError(_EMPTY), False, LoginRequired),
        (YtDownloadError(_EMPTY), True, LoginRequired),
        (YtDownloadError(_PARSE_JSON), True, LoginRequired),
        (YtDownloadError(_UNAVAILABLE), False, MediaNotFound),
        (_with_cause("ERROR: Unable to download", _HttpCause(429)), False, RateLimited),
        (ExtractorError("x", cause=_HttpCause(429)), False, RateLimited),
        # положительные контроли: то же без признака → общая ошибка
        (YtDownloadError(_PARSE_JSON), False, DownloadFailed),
        (_with_cause("ERROR: Unable to download", _HttpCause(500)), False, DownloadFailed),
        (YtDownloadError("ERROR: something new"), False, DownloadFailed),
        (YtDownloadError("ERROR: ffmpeg exited with code 1"), False, DownloadFailed),
        (OSError("disk"), False, DownloadFailed),
    ],
)
def test_translate_error(exc, session_lost, expected):
    error = translate_error(exc, session_lost=session_lost)
    assert type(error) is expected


def test_user_message_does_not_expose_ytdlp_text():
    error = translate_error(YtDownloadError(_PARSE_JSON), session_lost=False)
    assert "please report" not in error.user_message
    assert "yt-dlp" not in error.user_message
    assert "Failed to parse JSON" in str(error)  # причина — для лога


def test_describe_is_bounded_and_single_line():
    text = describe(YtDownloadError("a\n" + "x" * 1000))
    assert "\n" not in text
    assert len(text) < 400
    assert text.startswith("DownloadError: a x")


# --- граница yt-dlp ---------------------------------------------------------------------------


def test_boundary_translates_ytdlp_and_os_errors():
    with pytest.raises(LoginRequired), translating_errors():
        raise YtDownloadError(_EMPTY)
    with pytest.raises(DownloadFailed), translating_errors():
        raise OSError("нет места")


def test_boundary_passes_port_errors_cancel_and_own_bugs_unchanged():
    with pytest.raises(MediaNotFound), translating_errors():
        raise MediaNotFound("x")
    with pytest.raises(Cancelled), translating_errors():
        raise Cancelled
    with pytest.raises(KeyError), translating_errors():  # своя ошибка кода не маскируется
        raise KeyError("bug")


# --- cookies ----------------------------------------------------------------------------------


def _line(name: str, value: str, *, http_only: bool = False) -> str:
    domain = ("#HttpOnly_" if http_only else "") + ".instagram.com"
    return "\t".join([domain, "TRUE", "/", "TRUE", "1999999999", name, value])


def _cookies_text(*names: str) -> str:
    header = "# Netscape HTTP Cookie File\n\n"
    return header + "\n".join(_line(n, f"v-{n}", http_only=(n == SESSION_COOKIE)) for n in names)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src" / "instagram.txt"
    path.parent.mkdir()
    path.write_text(_cookies_text("csrftoken", SESSION_COOKIE, "mid"), encoding="utf-8")
    return path


def test_no_source_means_no_copy(tmp_path):
    assert copy_cookies(None, tmp_path) is None


def test_copy_is_private_inside_workdir_and_source_untouched(source, tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    before = _sha(source)
    copy = copy_cookies(source, workdir)
    assert copy is not None
    assert copy.path.parent == workdir
    assert stat.S_IMODE(copy.path.stat().st_mode) == 0o600
    assert _sha(copy.path) == before
    copy.path.write_text(_cookies_text("csrftoken"), encoding="utf-8")  # как перепишет yt-dlp
    assert _sha(source) == before  # исходник не меняется


def test_names_only_values_never_read(source):
    names = cookie_names(source)
    assert names == {"csrftoken", SESSION_COOKIE, "mid"}  # в т.ч. строка с #HttpOnly_
    assert not any(n.startswith("v-") for n in names)


def test_copy_repr_has_no_cookie_names(source, tmp_path):
    copy = copy_cookies(source, tmp_path)
    assert SESSION_COOKIE not in repr(copy)
    assert "csrftoken" not in repr(copy)


def test_unreadable_cookies_gives_no_names(tmp_path):
    assert cookie_names(tmp_path / "missing.txt") == set()


def test_session_lost_when_ytdlp_removed_sessionid(source, tmp_path):
    copy = copy_cookies(source, tmp_path)
    assert copy is not None
    assert copy.session_lost() is False  # ничего не менялось
    copy.path.write_text(_cookies_text("csrftoken", "mid"), encoding="utf-8")
    assert copy.session_lost() is True


def test_session_kept_is_not_lost_even_if_others_dropped(source, tmp_path):
    copy = copy_cookies(source, tmp_path)
    assert copy is not None
    copy.path.write_text(_cookies_text(SESSION_COOKIE), encoding="utf-8")  # datr и др. удалены
    assert copy.session_lost() is False


def test_no_session_before_means_not_lost(tmp_path):
    path = tmp_path / "c.txt"
    path.write_text(_cookies_text("csrftoken", "mid"), encoding="utf-8")
    workdir = tmp_path / "w"
    workdir.mkdir()
    copy = copy_cookies(path, workdir)
    assert copy is not None
    assert copy.session_lost() is False


def test_boundary_uses_session_lost_for_parse_json(source, tmp_path):
    """Решение 7: одна и та же ошибка «Failed to parse JSON» — по исчезновению sessionid."""
    copy = copy_cookies(source, tmp_path)
    assert copy is not None
    with pytest.raises(DownloadFailed), translating_errors(copy):  # sessionid на месте
        raise YtDownloadError(_PARSE_JSON)
    copy.path.write_text(_cookies_text("csrftoken"), encoding="utf-8")  # yt-dlp его удалил
    with pytest.raises(LoginRequired), translating_errors(copy):
        raise YtDownloadError(_PARSE_JSON)


def test_copy_refuses_to_overwrite_existing_target(source, tmp_path):
    copy_cookies(source, tmp_path)
    with pytest.raises(FileExistsError):
        copy_cookies(source, tmp_path)
