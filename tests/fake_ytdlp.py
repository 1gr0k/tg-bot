"""Фейк `yt_dlp.YoutubeDL` для тестов адаптеров: без сети и без настоящего yt-dlp.

Ведёт себя по пробам: `extract_info(process=False)` отдаёт сырой info dict; `process_ie_result`
качает видео в `outtmpl`; `max_filesize` — файл **молча** не создаётся (проба, notes); при выходе
перезаписывает `cookiefile`, как yt-dlp (теряет часть cookies). Пишет только в каталог из `outtmpl`
и в `cookiefile`.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size)


class Script:
    """Что «видит» yt-dlp в этом тесте."""

    def __init__(
        self,
        info: dict[str, Any] | None = None,
        *,
        video_size: int | dict[str, int] = 5_000_000,
        attempt_sizes: list[int] | None = None,
        photos: dict[str, bytes] | None = None,
        error: BaseException | None = None,
        photo_error: BaseException | None = None,
        cookies_after: str | None = None,
        ignore_max_filesize: bool = False,
        silent_skip: bool = False,
    ) -> None:
        self.info = info
        self.video_size = video_size
        self.attempt_sizes = attempt_sizes  # Shorts: размер файла на каждом уровне бюджета
        self.photos = photos or {}
        self.error = error
        self.photo_error = photo_error
        self.cookies_after = cookies_after  # во что yt-dlp «перепишет» cookiefile при выходе
        self.ignore_max_filesize = ignore_max_filesize
        # «файла нет» без сообщения о max_filesize (не «слишком большой», а сбой yt-dlp)
        self.silent_skip = silent_skip
        self.factory_calls: list[dict[str, Any]] = []  # опции каждого создания YoutubeDL
        self.extract_calls: list[dict[str, Any]] = []
        self.process_calls: list[str] = []
        self.urls_opened: list[str] = []

    def factory(self, options: dict[str, Any]) -> "FakeYDL":
        self.factory_calls.append(options)
        return FakeYDL(self, options)

    def size_for(self, entry_id: str) -> int:
        if isinstance(self.video_size, dict):
            return self.video_size[entry_id]
        return self.video_size


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int = -1) -> bytes:
        return self._data if size < 0 else self._data[:size]

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeYDL:
    def __init__(self, script: Script, options: dict[str, Any]) -> None:
        self._script = script
        self._options = options

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *exc: object) -> None:
        cookiefile = self._options.get("cookiefile")
        if cookiefile and self._script.cookies_after is not None:
            Path(cookiefile).write_text(self._script.cookies_after, encoding="utf-8")

    def _target(self, entry_id: str, ext: str = "mp4") -> Path:
        template = self._options["outtmpl"]
        return Path(template.replace("%(id)s", entry_id).replace("%(ext)s", ext))

    def _write_video(self, entry_id: str, size: int) -> Path | None:
        path = self._target(entry_id)
        limit = self._options.get("max_filesize")
        if limit is not None and size > limit and not self._script.ignore_max_filesize:
            logger = self._options.get("logger")
            if logger is not None and not self._script.silent_skip:
                # текст и канал — как у yt-dlp (проба 2026-10-13): `logger.debug`, ведущий `\r`
                logger.debug(
                    f"\r[download] File is larger than max-filesize ({size} bytes > {limit} bytes)."
                    " Aborting."
                )
            return None  # yt-dlp пропускает файл без ошибки (проба `max_filesize`)
        _sparse(path, size)
        return path

    def extract_info(self, url: str, download: bool = True, process: bool = True) -> Any:
        self._script.extract_calls.append({"url": url, "download": download, "process": process})
        if self._script.error is not None:
            raise self._script.error
        info = copy.deepcopy(self._script.info)
        if download and info is not None:  # Shorts: одна загрузка за вызов
            attempts = len([c for c in self._script.extract_calls if c["download"]])
            sizes = self._script.attempt_sizes or [5_000_000]
            path = self._write_video(info["id"], sizes[min(attempts, len(sizes)) - 1])
            info["requested_downloads"] = [{"filepath": str(path or self._target(info["id"]))}]
        return info

    def process_ie_result(self, entry: dict[str, Any], download: bool = True) -> dict[str, Any]:
        entry_id = entry["id"]
        self._script.process_calls.append(entry_id)
        path = self._write_video(entry_id, self._script.size_for(entry_id))
        return {
            "id": entry_id,
            "requested_downloads": [{"filepath": str(path or self._target(entry_id))}],
        }

    def urlopen(self, url: str) -> _Response:
        self._script.urls_opened.append(url)
        if self._script.photo_error is not None:
            raise self._script.photo_error
        return _Response(self._script.photos.get(url, b"\xff\xd8photo"))


def probe_by_name(
    table: dict[str, tuple[int | None, int | None, float | None]] | None = None,
) -> Callable:
    """Фейковый ffprobe: размеры по имени файла (по умолчанию — 720×1280, 73.3 с)."""
    from tg_bot.downloader.ytdlp import MediaProbe

    def probe(path: Path) -> MediaProbe:
        values = (table or {}).get(path.name) or (720, 1280, 73.3)
        return MediaProbe(*values)

    return probe
