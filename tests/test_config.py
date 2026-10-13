import logging
import os
from pathlib import Path

import pytest

from tg_bot.config import ConfigError, Settings, load_settings

# Фейковый токен собирается в рантайме: готового токена в репозитории нет (правила gitleaks).
_ID = "1234567890"
_BODY = "AA" + "abcdEFGH_ijkl-MNOPqrst0123456789xy"
TOKEN = f"{_ID}:{_BODY}"
OWNER = "424242"


def cookie_line(*, name: str = "csrftoken", value: str = "v", expires: str = "1999999999") -> str:
    return "\t".join([".instagram.com", "TRUE", "/", "TRUE", expires, name, value])


COOKIES_TEXT = "# Netscape HTTP Cookie File\n\n" + cookie_line() + "\n"


def env(**kwargs: str) -> dict[str, str]:
    base = {"BOT_TOKEN": TOKEN, "OWNER_ID": OWNER}
    base.update(kwargs)
    return base


# --- положительный контроль: корректные значения проходят, в том числе с пробелами и \r ------

VALID_ENVIRONS = [
    pytest.param(env(), id="чистые значения"),
    pytest.param(env(BOT_TOKEN=f"  {TOKEN}  ", OWNER_ID=f"  {OWNER}  "), id="пробелы по краям"),
    pytest.param(env(BOT_TOKEN=f"{TOKEN}\r", OWNER_ID=f"{OWNER}\r"), id="CRLF из .env"),
    pytest.param(env(BOT_TOKEN=f" \t{TOKEN}\r\n"), id="пробелы, таб, CRLF"),
    pytest.param(env(IG_COOKIES_FILE=""), id="IG_COOKIES_FILE пуст — как не задан"),
    pytest.param(env(IG_COOKIES_FILE="  \r"), id="IG_COOKIES_FILE из пробелов — как не задан"),
]


@pytest.mark.parametrize("environ", VALID_ENVIRONS)
def test_valid(environ: dict[str, str]) -> None:
    settings = load_settings(environ)
    assert settings == Settings(bot_token=TOKEN, owner_id=int(OWNER), ig_cookies_file=None)


def test_short_old_style_token_is_accepted() -> None:
    assert load_settings(env(BOT_TOKEN="123456:abc_DEF-ghi")).bot_token == "123456:abc_DEF-ghi"


# --- отрицательные строки: каждая → ConfigError с именем переменной, без значения ------------

BAD_TOKENS = [
    pytest.param(f"{TOKEN},", id="запятая после токена"),
    pytest.param(f"{TOKEN},\r", id="запятая и \\r"),
    pytest.param(f'"{TOKEN}"', id="двойные кавычки"),
    pytest.param(f"'{TOKEN}'", id="одинарные кавычки"),
    pytest.param(f"bot{TOKEN}", id="префикс bot"),
    pytest.param(f"{TOKEN} {_BODY}", id="два значения через пробел"),
    pytest.param(f"{_ID}{_BODY}", id="нет двоеточия"),
    pytest.param(f":{_BODY}", id="нет ID"),
    pytest.param(f"{_ID}:", id="нет второй части"),
    pytest.param(f"abc:{_BODY}", id="ID не из цифр"),
    pytest.param(f"{_ID}:{_BODY}!", id="лишний символ"),
    pytest.param(f"{_ID}:{_BODY}:{_BODY}", id="два двоеточия"),
    pytest.param("", id="пустое"),
    pytest.param("   \r", id="одни пробелы и \\r"),
]


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_bad_token(token: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env(BOT_TOKEN=token))
    message = str(excinfo.value)
    assert "BOT_TOKEN" in message
    assert _BODY not in message
    assert _ID not in message


def test_missing_token() -> None:
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings({"OWNER_ID": OWNER})


BAD_OWNER_IDS = [
    pytest.param("abc", id="не число"),
    pytest.param("12abc", id="число и хвост"),
    pytest.param("12.5", id="дробное"),
    pytest.param("1 2", id="пробел внутри"),
    pytest.param("-5", id="отрицательное"),
    pytest.param("+5", id="плюс"),
    pytest.param("１２３", id="цифры не ASCII"),  # noqa: RUF001 — полноширинные цифры нужны намеренно
    pytest.param(f"{OWNER},", id="запятая"),
    pytest.param(f'"{OWNER}"', id="кавычки"),
    pytest.param("", id="пустое"),
]


@pytest.mark.parametrize("owner_id", BAD_OWNER_IDS)
def test_bad_owner_id(owner_id: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env(OWNER_ID=owner_id))
    message = str(excinfo.value)
    assert "OWNER_ID" in message
    assert "BOT_TOKEN" not in message
    # Значение в текст ошибки не попадает (пустая строка входит в любую — её не проверяем).
    if owner_id.strip():
        assert owner_id.strip() not in message


def test_missing_owner_id() -> None:
    with pytest.raises(ConfigError, match="OWNER_ID"):
        load_settings({"BOT_TOKEN": TOKEN})


def test_nothing_set() -> None:
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings({})


# --- токен не попадает ни в repr, ни в текст ошибки ------------------------------------------


def test_token_is_not_in_repr_or_str() -> None:
    settings = load_settings(env())
    for text in (repr(settings), str(settings)):
        assert _BODY not in text  # TOKEN содержит _BODY: отдельная проверка TOKEN избыточна
        assert _ID not in text
    assert "owner_id=" in repr(settings)  # положительный контроль: repr остальные поля показывает


def test_token_is_not_in_error_with_cookies_problem(tmp_path: Path) -> None:
    missing = tmp_path / "нет-такого-файла.txt"
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env(IG_COOKIES_FILE=str(missing)))
    message = str(excinfo.value)
    assert "IG_COOKIES_FILE" in message
    # Токен уже проверен к этому моменту (он в окружении): ошибка про cookies не должна его нести.
    assert _BODY not in message
    assert _ID not in message


def test_token_is_not_in_log(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIES_TEXT)
    cookies.chmod(0o644)
    with caplog.at_level(logging.DEBUG):
        load_settings(env(IG_COOKIES_FILE=str(cookies)))
    assert caplog.records  # положительный контроль: предупреждение о правах записано
    assert _BODY not in caplog.text
    assert TOKEN not in caplog.text


# --- IG_COOKIES_FILE -------------------------------------------------------------------------


def test_cookies_file_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIES_TEXT)
    cookies.chmod(0o600)
    with caplog.at_level(logging.DEBUG):
        settings = load_settings(env(IG_COOKIES_FILE=f" {cookies}\r"))
    assert settings.ig_cookies_file == cookies
    assert not caplog.records


def test_cookies_file_wide_permissions_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIES_TEXT)
    cookies.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        settings = load_settings(env(IG_COOKIES_FILE=str(cookies)))
    assert settings.ig_cookies_file == cookies
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "IG_COOKIES_FILE" in caplog.text


def test_cookies_file_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="IG_COOKIES_FILE"):
        load_settings(env(IG_COOKIES_FILE=str(tmp_path / "nope.txt")))


def test_cookies_path_is_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="IG_COOKIES_FILE"):
        load_settings(env(IG_COOKIES_FILE=str(tmp_path)))


@pytest.mark.skipif(os.geteuid() == 0, reason="root читает любой файл")
def test_cookies_file_unreadable(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIES_TEXT)
    cookies.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="IG_COOKIES_FILE"):
            load_settings(env(IG_COOKIES_FILE=str(cookies)))
    finally:
        cookies.chmod(0o600)


# --- формат файла cookies (Netscape: 7 полей, expires — цифры) --------------------------------

# Значение cookie собрано в рантайме: ни в тексте ошибки, ни в логе его быть не должно.
_SECRET = "val-" + "0123456789abcdef" * 2


def _fields(*, n: int = 7, expires: str = "1999999999") -> str:
    fields = [".instagram.com", "TRUE", "/", "TRUE", expires, "sessionid", _SECRET, "extra"]
    return "\t".join(fields[:n])


_GOOD = cookie_line(name="mid")
_HTTP_ONLY = "#HttpOnly_" + cookie_line(name="sessionid", value=_SECRET)

VALID_COOKIES_FILES = [
    pytest.param(COOKIES_TEXT, id="один cookie"),
    pytest.param(f"# Netscape HTTP Cookie File\n\n   \n{_GOOD}\n{_HTTP_ONLY}\n", id="HttpOnly"),
    pytest.param(f"{_GOOD}\r\n{_HTTP_ONLY}\r\n", id="CRLF"),
    pytest.param(cookie_line(expires="0") + "\n", id="сессионный cookie, expires 0"),
    pytest.param(_GOOD, id="без перевода строки в конце"),
    pytest.param("", id="пустой файл"),
    pytest.param("# только комментарий\n", id="только комментарии"),
]


@pytest.mark.parametrize("text", VALID_COOKIES_FILES)
def test_cookies_file_valid_formats_pass(tmp_path: Path, text: str) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(text, encoding="utf-8", newline="")
    cookies.chmod(0o600)
    assert load_settings(env(IG_COOKIES_FILE=str(cookies))).ig_cookies_file == cookies


# Таблица «поле expires → принимает ли yt-dlp» — по коду, не по памяти: yt-dlp 2026.8.19,
# yt_dlp/cookies.py:1372 (`YoutubeDLCookieJar.load`): `if cookie.expires_at and not
# re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', cookie.expires_at): raise LoadError` — строка отбрасывается
# с предупреждением. Пустое значение (сессионный cookie) принимается; `config` — ровно так же.
EXPIRES_VS_YTDLP = [
    pytest.param("", True, id="пусто — сессионный cookie"),
    pytest.param("0", True, id="ноль"),
    pytest.param("1999999999", True, id="цифры"),
    pytest.param("1999999999.5", True, id="дробное"),
    pytest.param("abc", False, id="не число"),
    pytest.param("-1", False, id="отрицательное"),
    pytest.param("1.", False, id="точка без дробной части"),
    pytest.param(".5", False, id="нет целой части"),
    pytest.param("1e9", False, id="экспонента"),
    pytest.param(" ", False, id="пробел (непусто)"),
]


@pytest.mark.parametrize(("expires", "accepted"), EXPIRES_VS_YTDLP)
def test_cookies_expires_follows_ytdlp_rule(tmp_path: Path, expires: str, accepted: bool) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(f"{_fields(n=7, expires=expires)}\n", encoding="utf-8")
    cookies.chmod(0o600)
    if accepted:
        assert load_settings(env(IG_COOKIES_FILE=str(cookies))).ig_cookies_file == cookies
    else:
        with pytest.raises(ConfigError, match="expires"):
            load_settings(env(IG_COOKIES_FILE=str(cookies)))


BAD_COOKIES_FILES = [
    pytest.param(f"# c\n\n{_fields(n=6)}\n", 3, "7 полей", id="шесть полей"),
    pytest.param(f"{_GOOD}\n{_fields(n=8)}\n", 2, "7 полей", id="восемь полей"),
    pytest.param(f"{_GOOD}\n{_fields(n=1)}\n", 2, "7 полей", id="одно поле"),
    pytest.param(
        "\t".join(_fields().split("\t")[:3]) + " " + " ".join(_fields().split("\t")[3:]) + "\n",
        1,
        "7 полей",
        id="пробелы вместо табуляции",
    ),
    pytest.param(f"{_fields(expires='abc')}\n", 1, "expires", id="expires — буквы"),
    pytest.param(f"{_fields(expires='-1')}\n", 1, "expires", id="expires отрицательный"),
    pytest.param(f"{_fields(expires='12a')}\n", 1, "expires", id="expires: цифры и буква"),
    pytest.param(f"{_fields(expires='1.')}\n", 1, "expires", id="expires: точка без дробной части"),
    pytest.param(f"{_fields(expires='.5')}\n", 1, "expires", id="expires: нет целой части"),
    pytest.param(f"{_fields(expires='1.2.3')}\n", 1, "expires", id="expires: две точки"),
    pytest.param(f"{_fields(expires=' ')}\n", 1, "expires", id="expires: пробел (непусто)"),
    pytest.param(
        f"{_GOOD}\r\n{_HTTP_ONLY}\r\n{_fields(n=6)}\r\n", 3, "7 полей", id="CRLF, строка 3"
    ),
    pytest.param(f"#HttpOnly_{_fields(expires='x')}\n", 1, "expires", id="HttpOnly, expires"),
]


@pytest.mark.parametrize(("text", "line", "reason"), BAD_COOKIES_FILES)
def test_cookies_file_bad_format_is_config_error_with_line_number(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, text: str, line: int, reason: str
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(text, encoding="utf-8", newline="")
    cookies.chmod(0o600)
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as excinfo:
        load_settings(env(IG_COOKIES_FILE=str(cookies)))
    message = str(excinfo.value)
    assert "IG_COOKIES_FILE" in message
    assert f"строка {line}:" in message
    assert reason in message
    # значение cookie и токен — ни в ошибке, ни в логе, ни в цепочке исключений
    assert _SECRET not in message and _SECRET not in caplog.text
    assert _BODY not in message
    assert excinfo.value.__cause__ is None and excinfo.value.__context__ is None


def test_cookies_format_checked_before_permission_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Положительный контроль к тексту ошибки: тот же файл с корректной строкой проходит."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(_fields(n=6) + "\n")
    cookies.chmod(0o644)
    with caplog.at_level(logging.WARNING), pytest.raises(ConfigError):
        load_settings(env(IG_COOKIES_FILE=str(cookies)))
    assert _SECRET not in caplog.text
    cookies.write_text(_fields() + "\n")
    assert load_settings(env(IG_COOKIES_FILE=str(cookies))).ig_cookies_file == cookies
