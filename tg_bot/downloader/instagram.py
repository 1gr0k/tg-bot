"""Загрузчик Instagram (Reels, посты, карусели) поверх yt-dlp — решения 5–7, 9 плана.

Разбор info dict — чистые функции наверху (тестируются на фикстурах `tests/fixtures/`), загрузка —
в конце. Тип публикации определяется по ответу yt-dlp, а не по пути ссылки (решение 5): элемент с
непустым `formats` — видео, с пустым — фото (решение 6), несколько `entries` — карусель.

Читатели info dict (аудит — notes плана, «Аудит читателей»): `entries`, `formats`, `thumbnails`,
`thumbnails[].url` (параметр `stp`), `description`, `id`; после загрузки видео —
`requested_downloads[].filepath`. Незнакомое, `None` и неверный тип — пропуск с записью в лог.
"""

import logging
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yt_dlp

from tg_bot.downloader.port import (
    DownloadResult,
    MediaItem,
    MediaKind,
    MediaTooLarge,
    NoMedia,
)
from tg_bot.downloader.ytdlp import (
    SEND_LIMIT_BYTES,
    Cancelled,
    ThreadRunner,
    base_options,
    copy_cookies,
    ensure_fits,
    probe_media,
    translating_errors,
)
from tg_bot.links import Link

log = logging.getLogger(__name__)

# Решение 5: прогрессивный H.264 + AAC в первую очередь; не зависит от `format_id`.
FORMAT_SELECTOR = "b[format_id!*=dash]/bv*[vcodec^=avc1]+ba/bv*+ba/b"
# Решение 6: короткая сторона скачанного фото меньше — взят не оригинал → WARNING.
MIN_PHOTO_SIDE = 600

# Не больше 5 цифр: гигантское число в чужом URL не должно ронять `int()`.
_SIZE_MARKER = re.compile(r"(?:^|_)[ps](\d{1,5})x(\d{1,5})(?:_|$)")
_PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

type YdlFactory = Callable[[dict[str, Any]], AbstractContextManager[Any]]
type Probe = Callable[[Path], Any]


# --- разбор info dict (чистые функции) --------------------------------------------------------


def entries_of(info: object) -> list[dict[str, Any]]:
    """Элементы публикации: `entries` карусели или сам `info` (Reel, одно фото).

    `entries` не список или `None` → `info` как одиночный; в списке не-словари пропускаются.
    """
    if not isinstance(info, dict):
        log.warning("info dict не словарь (%s) — нет элементов", type(info).__name__)
        return []
    entries = info.get("entries")
    if not isinstance(entries, list):
        return [info]
    result = []
    for position, entry in enumerate(entries, start=1):
        if isinstance(entry, dict):
            result.append(entry)
        else:
            log.warning("entries[%d] — %s, пропуск", position, type(entry).__name__)
    return result


def classify(entry: dict[str, Any]) -> MediaKind | None:
    """Непустой `formats` — видео; пустой или нет при наличии `thumbnails` — фото; иначе None."""
    formats = entry.get("formats")
    if isinstance(formats, list) and formats:
        return MediaKind.VIDEO
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        return MediaKind.PHOTO
    return None


def _size_marker(url: str) -> tuple[int, int] | None:
    """Размер из маркера `[ps]<w>x<h>` в параметре `stp`; нет маркера — None."""
    stp = parse_qs(urlsplit(url).query).get("stp", [""])[0]
    found = _SIZE_MARKER.search(stp)
    return (int(found.group(1)), int(found.group(2))) if found else None


def pick_photo_url(thumbnails: object) -> str | None:
    """Оригинал фото (решение 6): последний вариант без маркера размера, иначе наибольший маркер.

    У `thumbnails` нет `width/height` — размер только в `stp`. Нет подходящих URL — None.
    """
    if not isinstance(thumbnails, list):
        return None
    unmarked: str | None = None
    largest: tuple[int, str] | None = None
    for thumbnail in thumbnails:
        url = thumbnail.get("url") if isinstance(thumbnail, dict) else None
        if not isinstance(url, str) or not url:
            continue
        marker = _size_marker(url)
        if marker is None:
            unmarked = url
        elif largest is None or marker[0] * marker[1] >= largest[0]:
            largest = (marker[0] * marker[1], url)
    if unmarked is not None:
        return unmarked
    return largest[1] if largest else None


def caption_of(info: object) -> str:
    """Описание: верхний уровень, иначе первого элемента с описанием; нет — пустая строка."""
    candidates = [info]
    if isinstance(info, dict) and isinstance(info.get("entries"), list):
        candidates.extend(info["entries"])
    for candidate in candidates:
        text = candidate.get("description") if isinstance(candidate, dict) else None
        if isinstance(text, str) and text.strip():
            return text
    return ""


def downloaded_path(result: object) -> Path | None:
    """Путь файла после `process_ie_result`: `requested_downloads[].filepath`; нет — None."""
    if not isinstance(result, dict):
        return None
    downloads = result.get("requested_downloads")
    for download in downloads if isinstance(downloads, list) else []:
        filepath = download.get("filepath") if isinstance(download, dict) else None
        if isinstance(filepath, str) and filepath:
            return Path(filepath)
    return None


def _photo_suffix(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in _PHOTO_SUFFIXES else ".jpg"


# --- загрузчик -------------------------------------------------------------------------------


class InstagramDownloader:
    """Порт `Downloader` для Instagram. Блокирующая работа — в потоке `ThreadRunner`."""

    def __init__(
        self,
        runner: ThreadRunner,
        cookies_file: Path | None = None,
        *,
        ydl_factory: YdlFactory = yt_dlp.YoutubeDL,
        probe: Probe = probe_media,
    ) -> None:
        self._runner = runner
        self._cookies_file = cookies_file
        self._ydl_factory = ydl_factory
        self._probe = probe

    async def download(self, link: Link, workdir: Path) -> DownloadResult:
        return await self._runner.run(lambda cancel: self._download_sync(link, workdir, cancel))

    def _download_sync(self, link: Link, workdir: Path, cancel: threading.Event) -> DownloadResult:
        cookies = None
        with translating_errors():  # ошибка чтения/записи копии cookies тоже переводится
            cookies = copy_cookies(self._cookies_file, workdir)
        with translating_errors(cookies):
            options = base_options(cancel, cookies=cookies) | {
                "ignore_no_formats_error": True,  # без него пост-фото и смешанная карусель падают
                "format": FORMAT_SELECTOR,
                "outtmpl": str(workdir / "%(id)s.%(ext)s"),
            }
            with self._ydl_factory(options) as ydl:
                info = ydl.extract_info(link.url, download=False, process=False)
                items = [
                    item
                    for position, entry in enumerate(entries_of(info), start=1)
                    if (item := self._fetch_entry(ydl, entry, position, workdir, cancel))
                ]
            if not items:
                raise NoMedia("в публикации нет ни фото, ни видео")
            log.info(
                "instagram %s (%s): %d элем. [%s]",
                link.media_id,
                link.kind,
                len(items),
                ", ".join(i.kind for i in items),
            )
            return DownloadResult(items=tuple(items), caption=caption_of(info))

    def _fetch_entry(
        self, ydl: Any, entry: dict[str, Any], position: int, workdir: Path, cancel: threading.Event
    ) -> MediaItem | None:
        if cancel.is_set():
            raise Cancelled
        kind = classify(entry)
        if kind is MediaKind.VIDEO:
            return self._fetch_video(ydl, entry, position)
        if kind is MediaKind.PHOTO:
            return self._fetch_photo(ydl, entry, position, workdir)
        log.warning("элемент %d: ни форматов, ни превью — пропуск", position)
        return None

    def _fetch_video(self, ydl: Any, entry: dict[str, Any], position: int) -> MediaItem | None:
        result = ydl.process_ie_result(entry, download=True)
        path = downloaded_path(result)
        if path is None:
            log.warning("элемент %d: yt-dlp не вернул путь файла — пропуск", position)
            return None
        if not path.exists():  # `max_filesize`: yt-dlp пропустил файл без ошибки (проба)
            raise MediaTooLarge(f"элемент {position}: yt-dlp пропустил файл по max_filesize")
        ensure_fits(path)
        probe = self._probe(path)
        return MediaItem(MediaKind.VIDEO, path, probe.width, probe.height, probe.duration)

    def _fetch_photo(
        self, ydl: Any, entry: dict[str, Any], position: int, workdir: Path
    ) -> MediaItem | None:
        url = pick_photo_url(entry.get("thumbnails"))
        if url is None:
            log.warning("элемент %d: для фото нет пригодного URL — пропуск", position)
            return None
        path = workdir / f"{position:02d}_photo{_photo_suffix(url)}"
        with ydl.urlopen(url) as response:
            data = response.read(SEND_LIMIT_BYTES + 1)
        path.write_bytes(data)
        ensure_fits(path)
        probe = self._probe(path)
        short = min(probe.width, probe.height) if probe.width and probe.height else None
        if short is not None and short < MIN_PHOTO_SIDE:
            log.warning(
                "элемент %d: фото %dx%d, короткая сторона меньше %d — вероятно, взят не оригинал "
                "(смена формата Instagram или yt-dlp?)",
                position,
                probe.width,
                probe.height,
                MIN_PHOTO_SIDE,
            )
        return MediaItem(MediaKind.PHOTO, path, probe.width, probe.height)
