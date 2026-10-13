"""Порт загрузчика: интерфейс, структуры результата, иерархия ошибок (решение 4, раздел 5 плана).

Без типов yt-dlp и aiogram: остальной код зависит только от этого модуля. Временный каталог
принадлежит вызывающему (решение 3): загрузчик пишет только внутрь переданного `workdir`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol

from tg_bot.links import Link


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class MediaItem:
    kind: MediaKind
    path: Path  # внутри workdir
    width: int | None = None
    height: int | None = None
    duration: float | None = None  # секунды, для видео


@dataclass(frozen=True, slots=True)
class DownloadResult:
    items: tuple[MediaItem, ...]  # исходный порядок, не пусто
    caption: str = ""  # "" — описания нет
    # Элементов публикации, которые не удалось скачать или которые не годятся (сверх лимита,
    # без пригодных данных): они пропущены, остальные в `items`. «Не удалось N из M» — N = skipped,
    # M = len(items) + skipped.
    skipped: int = 0
    # Причина, о которой нужно сообщить владельцу, даже когда загрузка удалась (решение 7):
    # `LoginRequired` — сессия cookies потеряна или элемент упал из-за входа; `RateLimited` —
    # элемент упал по лимиту запросов; `None` — сигнала нет. Пользователю не показывается;
    # уведомление владельцу по этому полю — часть C.
    owner_alert: "type[DownloadError] | None" = None

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("DownloadResult: items не может быть пустым")
        if self.skipped < 0:
            raise ValueError("DownloadResult: skipped не может быть отрицательным")
        if self.owner_alert not in (None, LoginRequired, RateLimited):
            raise ValueError("DownloadResult: owner_alert — LoginRequired, RateLimited или None")


class DownloadError(Exception):
    """Базовая ошибка загрузки.

    `user_message` — короткий текст пользователю (без текста yt-dlp); `str(error)` — причина для
    лога (без значений cookies и токенов).
    """

    user_message: ClassVar[str] = "Скачать не удалось."


class MediaNotFound(DownloadError):
    """Удалено или не существует."""

    user_message = "Публикация не найдена: возможно, она удалена."


class LoginRequired(DownloadError):
    """Нужен вход или cookies отвергнуты (без cookies «удалено» и «нужен вход» не различить)."""

    user_message = "Публикация недоступна без входа или удалена."


class RateLimited(DownloadError):
    user_message = "Слишком много запросов, попробуйте позже."


class MediaTooLarge(DownloadError):
    user_message = "Файл слишком большой для отправки в Telegram."


class DownloadTimeout(DownloadError):
    user_message = "Загрузка заняла слишком много времени."


class NoMedia(DownloadError):
    """Публикация без поддерживаемых медиа."""

    user_message = "Поддерживаемых медиа в публикации нет."


class DownloadFailed(DownloadError):
    """Прочее, в том числе ошибка FFmpeg и незнакомая ошибка yt-dlp."""

    user_message = "Скачать не удалось."


def owner_alert_of(
    errors: Iterable[DownloadError], *, session_lost: bool
) -> "type[DownloadError] | None":
    """Сигнал владельцу по ошибкам пропущенных элементов: потеря сессии или `LoginRequired`
    важнее `RateLimited`; прочие ошибки сигнала не дают."""
    kinds = {type(error) for error in errors}
    if session_lost or LoginRequired in kinds:
        return LoginRequired
    return RateLimited if RateLimited in kinds else None


class Downloader(Protocol):
    async def download(self, link: Link, workdir: Path) -> DownloadResult: ...
