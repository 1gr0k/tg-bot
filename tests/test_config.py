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
    cookies.write_text("x")
    cookies.chmod(0o644)
    with caplog.at_level(logging.DEBUG):
        load_settings(env(IG_COOKIES_FILE=str(cookies)))
    assert caplog.records  # положительный контроль: предупреждение о правах записано
    assert _BODY not in caplog.text
    assert TOKEN not in caplog.text


# --- IG_COOKIES_FILE -------------------------------------------------------------------------


def test_cookies_file_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("x")
    cookies.chmod(0o600)
    with caplog.at_level(logging.DEBUG):
        settings = load_settings(env(IG_COOKIES_FILE=f" {cookies}\r"))
    assert settings.ig_cookies_file == cookies
    assert not caplog.records


def test_cookies_file_wide_permissions_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("x")
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
    cookies.write_text("x")
    cookies.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="IG_COOKIES_FILE"):
            load_settings(env(IG_COOKIES_FILE=str(cookies)))
    finally:
        cookies.chmod(0o600)
