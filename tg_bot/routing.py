"""Маршрутизация: ссылка → загрузчик по платформе (ТЗ 6.2).

Тип публикации (`/p/` — фото, карусель или видео) здесь не учитывается: его определяет загрузчик
по ответу yt-dlp (решение 5 плана).
"""

from collections.abc import Mapping

from tg_bot.downloader.port import Downloader, DownloadError
from tg_bot.links import Link, Platform


class UnsupportedLink(DownloadError):
    """Для платформы ссылки нет загрузчика. Наружу — вместо `KeyError`."""

    user_message = "Такие ссылки пока не поддерживаются."


class DownloaderRegistry:
    def __init__(self, by_platform: Mapping[Platform, Downloader]) -> None:
        self._by_platform = dict(by_platform)  # копия: правки исходного словаря не влияют

    def for_link(self, link: Link) -> Downloader:
        try:
            return self._by_platform[link.platform]
        except KeyError:
            raise UnsupportedLink(f"нет загрузчика для платформы {link.platform}") from None
