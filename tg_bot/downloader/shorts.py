"""Загрузчик YouTube Shorts поверх yt-dlp — решения 5, 9, 17 плана.

Выбор формата: H.264 + AAC (`format_sort`), merge через FFmpeg без перекодировки, бюджет размера в
селекторе с повтором на следующем уровне, если факт после загрузки больше лимита Telegram.

Читатели info dict (аудит — notes плана): `description`, `title`, `requested_downloads[].filepath`.
Метаданные видео (`width`/`height`/`duration`) — из ffprobe по файлу, а не из info dict.
"""

import logging
import threading
from pathlib import Path
from typing import Any

import yt_dlp

from tg_bot.downloader.instagram import Probe, YdlFactory, downloaded_path
from tg_bot.downloader.port import DownloadFailed, DownloadResult, MediaItem, MediaKind
from tg_bot.downloader.ytdlp import (
    Budget,
    ThreadRunner,
    YdlLogger,
    base_options,
    fit_budget,
    probe_media,
    translating_errors,
)
from tg_bot.links import Link

log = logging.getLogger(__name__)

# Решение 5: H.264 + AAC; `res` в yt-dlp — меньшая сторона кадра, поэтому вертикальное видео
# не режется; оригинальная аудиодорожка выбирается полем `lang` сортировки yt-dlp.
FORMAT_SORT = ["vcodec:h264", "acodec:aac", "res:1080", "ext:mp4:m4a"]


def caption_of(info: object) -> str:
    """Описание ролика; пустое — название; ничего нет — пустая строка (ТЗ 2.4: «если доступно»)."""
    if not isinstance(info, dict):
        return ""
    for key in ("description", "title"):
        text = info.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return ""


class ShortsDownloader:
    """Порт `Downloader` для YouTube Shorts. Блокирующая работа — в потоке `ThreadRunner`."""

    def __init__(
        self,
        runner: ThreadRunner,
        *,
        ydl_factory: YdlFactory = yt_dlp.YoutubeDL,
        probe: Probe = probe_media,
    ) -> None:
        self._runner = runner
        self._ydl_factory = ydl_factory
        self._probe = probe

    async def download(self, link: Link, workdir: Path) -> DownloadResult:
        return await self._runner.run(lambda cancel: self._download_sync(link, workdir, cancel))

    def _download_sync(self, link: Link, workdir: Path, cancel: threading.Event) -> DownloadResult:
        infos: list[Any] = []

        def attempt(budget: Budget, subdir: Path) -> Path | None:
            logger = YdlLogger()
            options = base_options(cancel, logger=logger) | {
                "format": budget.selector,
                "format_sort": FORMAT_SORT,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "outtmpl": str(subdir / "%(id)s.%(ext)s"),
            }
            with self._ydl_factory(options) as ydl:
                info = ydl.extract_info(link.url, download=True)
            infos.append(info)
            path = downloaded_path(info)
            if path is None:  # неизвестный путь — не «слишком большой»
                raise DownloadFailed("yt-dlp не вернул путь скачанного файла")
            if path.exists():
                return path
            if logger.take_size_exceeded():  # yt-dlp сообщил: файл больше max_filesize
                return None
            raise DownloadFailed("yt-dlp не создал файл и не сообщил, что файл больше max_filesize")

        with translating_errors():
            path = fit_budget(attempt, workdir, cancel)
            probe = self._probe(path)
            log.info("shorts %s: %s байт", link.media_id, path.stat().st_size)
            item = MediaItem(MediaKind.VIDEO, path, probe.width, probe.height, probe.duration)
            return DownloadResult(items=(item,), caption=caption_of(infos[-1]))
