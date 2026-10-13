"""Фейк загрузчика для тестов pipeline и обработчиков (часть C).

`FakeDownloader` ведёт себя как реальные адаптеры (`InstagramDownloader`, `ShortsDownloader`):
те же типы ошибок (только наследники `DownloadError`), элементы в заданном порядке, файлы — только
внутри переданного `workdir`, пустой результат — `NoMedia`. Элемент больше лимита Telegram или с
`error` пропускается и считается в `skipped`; `owner_alert` ставится по тем же правилам, что у
`InstagramDownloader` (`owner_alert_of`; `session_lost=True` — сессия cookies потеряна); не удался
ни один — ошибка первого (при потере сессии — `LoginRequired`). Контракт общий с реальными
адаптерами проверяет `tests/test_downloader_contract.py`.

Два отличия от реальных: ошибка выбрасывается **до** записи файлов (реальный адаптер может оставить
в `workdir` копию cookies и частичные файлы — очистка каталога обязана с этим справляться), и
`gate` — точка, на которой тест удерживает загрузку «в процессе» (семафор, повтор ссылки, отмена).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tg_bot.downloader.port import (
    DownloadError,
    DownloadFailed,
    DownloadResult,
    LoginRequired,
    MediaItem,
    MediaKind,
    MediaTooLarge,
    NoMedia,
    owner_alert_of,
)
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES
from tg_bot.links import Link


@dataclass(frozen=True, slots=True)
class FakeMedia:
    kind: MediaKind
    name: str  # имя файла внутри workdir, без каталогов
    size: int = 1_000
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    error: DownloadError | None = None  # элемент не скачался: пропускается, как у адаптера

    def __post_init__(self) -> None:
        if self.error is not None and not isinstance(self.error, DownloadError):
            raise TypeError("FakeMedia: ошибка должна быть наследником DownloadError")


def video(
    name: str = "v.mp4",
    size: int = 1_000,
    *,
    width=720,
    height=1280,
    duration=10.0,
    error: DownloadError | None = None,
) -> FakeMedia:
    return FakeMedia(MediaKind.VIDEO, name, size, width, height, duration, error)


def photo(
    name: str = "p.jpg",
    size: int = 1_000,
    *,
    width=1080,
    height=1350,
    error: DownloadError | None = None,
) -> FakeMedia:
    return FakeMedia(MediaKind.PHOTO, name, size, width, height, error=error)


class FakeDownloader:
    def __init__(
        self,
        items: Sequence[FakeMedia] = (),
        caption: str = "",
        error: DownloadError | None = None,
        *,
        gate: asyncio.Event | None = None,
        session_lost: bool = False,
    ) -> None:
        if error is not None and not isinstance(error, DownloadError):
            raise TypeError("FakeDownloader: ошибка должна быть наследником DownloadError")
        self._session_lost = session_lost  # yt-dlp при `close()` убрал `sessionid` из cookies
        self._items = tuple(items)
        self._caption = caption
        self._error = error
        self._gate = gate
        self.calls: list[tuple[Link, Path]] = []
        self.started = asyncio.Event()  # загрузка дошла до `gate`
        self.cancelled = False

    async def download(self, link: Link, workdir: Path) -> DownloadResult:
        self.calls.append((link, workdir))
        self.started.set()
        if self._gate is not None:
            try:
                await self._gate.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self._error is not None:
            raise self._error
        # как у реальных адаптеров: файловый ввод-вывод — в потоке
        return await asyncio.to_thread(self._build, workdir)

    def _build(self, workdir: Path) -> DownloadResult:
        if not workdir.is_dir():
            raise DownloadFailed(f"каталог загрузки не существует: {workdir.name}")
        if not self._items:
            raise NoMedia("в публикации нет ни фото, ни видео")
        kept: list[FakeMedia] = []
        errors: list[DownloadError] = []
        for media in self._items:
            if media.error is not None:
                errors.append(media.error)
            elif media.size > SEND_LIMIT_BYTES:  # как `ensure_fits` в адаптерах: и видео, и фото
                errors.append(MediaTooLarge(f"{media.name}: {media.size} байт больше лимита"))
            else:
                kept.append(media)
        if not kept:  # ни один не удался: как адаптер — причина первого, но потеря сессии важнее
            first = errors[0]
            if self._session_lost and not isinstance(first, LoginRequired):
                raise LoginRequired("сессия cookies потеряна") from first
            raise first
        paths = [self._write(workdir, media) for media in kept]
        items = tuple(
            MediaItem(m.kind, path, m.width, m.height, m.duration)
            for m, path in zip(kept, paths, strict=True)
        )
        return DownloadResult(
            items=items,
            caption=self._caption,
            skipped=len(errors),
            owner_alert=owner_alert_of(errors, session_lost=self._session_lost),
        )

    @staticmethod
    def _write(workdir: Path, media: FakeMedia) -> Path:
        if Path(media.name).name != media.name or media.name in ("", ".", ".."):
            raise ValueError(f"FakeMedia.name — имя файла, не путь: {media.name!r}")
        path = workdir / media.name
        with path.open("wb") as fh:
            fh.truncate(media.size)  # разреженный файл нужного размера
        return path
