"""Конфигурация из переменных окружения; проверка при старте.

В сообщениях `ConfigError` — имя переменной и причина, но не значение: значения секретов не
попадают ни в ошибки, ни в `repr` настроек, ни в лог.
"""

import logging
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# <цифры>:<символы [0-9A-Za-z_-]> целиком: без префикса `bot`, кавычек, запятой.
_TOKEN_RE = re.compile(r"[0-9]+:[0-9A-Za-z_-]+")
_OWNER_ID_RE = re.compile(r"[0-9]+")


class ConfigError(Exception):
    """Некорректная конфигурация. Текст — имя переменной и причина, без значения."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    owner_id: int
    ig_cookies_file: Path | None = None


def _read(environ: Mapping[str, str], name: str) -> str | None:
    """Значение без пробелов и `\\r` по краям (`.env` с CRLF); нет переменной — None."""
    value = environ.get(name)
    return None if value is None else value.strip()


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _read(environ, name)
    if value is None:
        raise ConfigError(f"{name}: переменная не задана")
    if not value:
        raise ConfigError(f"{name}: значение пустое")
    return value


def _bot_token(environ: Mapping[str, str]) -> str:
    token = _required(environ, "BOT_TOKEN")
    if not _TOKEN_RE.fullmatch(token):
        raise ConfigError(
            "BOT_TOKEN: неверный формат, ожидается <цифры>:<буквы, цифры, _ и -> "
            "целиком, без префикса bot, кавычек и запятой"
        )
    return token


def _owner_id(environ: Mapping[str, str]) -> int:
    value = _required(environ, "OWNER_ID")
    if not _OWNER_ID_RE.fullmatch(value):
        raise ConfigError("OWNER_ID: ожидается целое число (id пользователя)")
    return int(value)


def _cookies_file(environ: Mapping[str, str]) -> Path | None:
    value = _read(environ, "IG_COOKIES_FILE")
    if not value:
        return None  # не задан — Instagram без cookies
    path = Path(value)
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ConfigError(
            f"IG_COOKIES_FILE: файл не найден или недоступен ({exc.strerror})"
        ) from None
    if not stat.S_ISREG(mode):
        raise ConfigError("IG_COOKIES_FILE: это не обычный файл")
    if not os.access(path, os.R_OK):
        raise ConfigError("IG_COOKIES_FILE: файл не читается")
    if stat.S_IMODE(mode) & 0o077:
        log.warning(
            "IG_COOKIES_FILE: права файла %s шире 600 — cookies доступны другим пользователям",
            oct(stat.S_IMODE(mode)),
        )
    return path


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Настройки из окружения; `ConfigError` — на первую же проблему (имя и причина)."""
    return Settings(
        bot_token=_bot_token(environ),
        owner_id=_owner_id(environ),
        ig_cookies_file=_cookies_file(environ),
    )
