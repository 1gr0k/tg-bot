"""Общий код адаптеров yt-dlp: запуск в потоке с тайм-аутом, бюджет размера, перевод ошибок,
копия cookies, ffprobe (решения 5–9 плана).

yt-dlp — только как библиотека. Всё блокирующее выполняется в потоке `ThreadRunner`.

**Решение 8 (изменено в части B).** Progress-hook yt-dlp прерывает загрузку, но не вызывается на
фазе извлечения (проба: notes, «Итог части B») — поток на извлечении прервать нельзя. Поэтому при
тайм-ауте `ThreadRunner` ставит флаг отмены и **ждёт завершения потока**, и только потом
выбрасывает `DownloadTimeout`: каталог загрузки (его удаляет вызывающий в `finally`) и семафор
освобождаются после конца потока.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_dlp.utils import YoutubeDLError

from tg_bot.downloader.port import (
    DownloadError,
    DownloadFailed,
    DownloadTimeout,
    LoginRequired,
    MediaNotFound,
    MediaTooLarge,
    RateLimited,
)

log = logging.getLogger(__name__)

# Решение 8: тайм-аут загрузки одной ссылки и сокет-тайм-аут yt-dlp (проба 6: 3–27 с, запас ×4).
DOWNLOAD_TIMEOUT = 120.0
SOCKET_TIMEOUT = 20
# Порог: поток после флага отмены живёт дольше — WARNING; при остановке бота столько же ждём.
THREAD_GRACE = 3.0 * SOCKET_TIMEOUT
# Решение 9: одновременных загрузок.
MAX_CONCURRENT_DOWNLOADS = 2
# Решение 9, проба 1: лимит видео в облачном Bot API, байт.
SEND_LIMIT_BYTES = 52_428_800


class Cancelled(Exception):
    """Поднимается из progress-hook, когда выставлен флаг отмены (прерывает загрузку yt-dlp)."""


def progress_hook(cancel: threading.Event) -> Callable[[dict[str, Any]], None]:
    """Хук для `progress_hooks`: исключение из него прерывает загрузку (но не извлечение)."""

    def hook(_status: dict[str, Any]) -> None:
        if cancel.is_set():
            raise Cancelled

    return hook


class ThreadRunner:
    """Запуск блокирующей функции в своём пуле с тайм-аутом, флагом отмены и семафором.

    `func(cancel)` получает `threading.Event` — флаг отмены; функция (или progress-hook) сама его
    проверяет. Семафор и пул имеют один размер `limit` (решение 9: 2) и освобождаются только когда
    поток завершился.
    """

    def __init__(
        self,
        *,
        limit: int = MAX_CONCURRENT_DOWNLOADS,
        timeout: float = DOWNLOAD_TIMEOUT,
        grace: float = THREAD_GRACE,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._timeout = timeout
        self._grace = grace
        self._sem = asyncio.Semaphore(limit)
        # Свой пул, а не пул по умолчанию (решение 8); `executor` — только для тестов.
        self._executor = executor or ThreadPoolExecutor(
            max_workers=limit, thread_name_prefix="ytdlp"
        )

    def close(self) -> None:
        """Остановка бота: новых задач нет, ждать потоки не нужно (их ждёт `run`)."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def run[T](self, func: Callable[[threading.Event], T]) -> T:
        async with self._sem:
            cancel = threading.Event()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, func, cancel)
            started = loop.time()
            try:
                try:
                    async with asyncio.timeout(self._timeout):
                        return await asyncio.shield(future)
                except TimeoutError:
                    cancel.set()
                    await self._join(future, started)
                    raise DownloadTimeout(f"тайм-аут {self._timeout:g} сек") from None
            except asyncio.CancelledError:
                cancel.set()
                await self._join_bounded(future, started)
                raise

    async def _join(self, future: "asyncio.Future[Any]", started: float) -> None:
        """Ждёт конец потока после флага отмены; после порога — WARNING, ожидание продолжается."""
        loop = asyncio.get_running_loop()
        done, _ = await asyncio.wait({future}, timeout=self._grace)
        if not done:
            log.warning(
                "поток yt-dlp жив через %.0f сек после флага отмены (всего %.0f сек); ждём",
                self._grace,
                loop.time() - started,
            )
            await asyncio.wait({future})
        self._consume(future)

    async def _join_bounded(self, future: "asyncio.Future[Any]", started: float) -> None:
        """Остановка бота: ждём поток не дольше порога; не успел — ERROR."""
        done, _ = await asyncio.wait({future}, timeout=self._grace)
        if done:
            self._consume(future)
            return
        log.error(
            "поток yt-dlp не завершился за %.0f сек после отмены (идёт %.0f сек); каталог загрузки "
            "будет удалён, пока поток жив",
            self._grace,
            asyncio.get_running_loop().time() - started,
        )

    @staticmethod
    def _consume(future: "asyncio.Future[Any]") -> None:
        """Забирает исключение потока, чтобы asyncio не жаловался; оно уже не нужно."""
        if not future.cancelled() and (exc := future.exception()) is not None:
            log.debug("поток после флага отмены завершился ошибкой %s", type(exc).__name__)


# --- бюджет размера (решение 9) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Budget:
    """Уровень бюджета, байт: `video`/`video_approx` — раздельные потоки, `total`/`total_approx` —
    готовый формат (по `filesize` и по `filesize_approx` соответственно)."""

    video: int
    video_approx: int
    total: int
    total_approx: int

    @property
    def selector(self) -> str:
        return (
            f"bv*[filesize<{self.video}]+ba/bv*[filesize_approx<{self.video_approx}]+ba/"
            f"b[filesize<{self.total}]/b[filesize_approx<{self.total_approx}]/bv*+ba/b"
        )


BUDGETS = (
    Budget(45_000_000, 40_000_000, 48_000_000, 43_000_000),
    Budget(25_000_000, 22_000_000, 28_000_000, 25_000_000),
    Budget(12_000_000, 10_000_000, 14_000_000, 12_000_000),
)


def fit_budget(
    attempt: Callable[[Budget, Path], Path | None],
    workdir: Path,
    cancel: threading.Event,
    *,
    limit: int = SEND_LIMIT_BYTES,
) -> Path:
    """Загрузка с проверкой фактического размера: больше `limit` — следующий уровень бюджета.

    `attempt(budget, subdir)` качает в новый подкаталог `workdir` и возвращает файл; `None` — yt-dlp
    сам пропустил файл по `max_filesize` (проба: без исключения и без файла) — это «слишком
    большой». Уровни кончились — `MediaTooLarge`.
    """
    for number, budget in enumerate(BUDGETS, start=1):
        if cancel.is_set():
            raise Cancelled
        subdir = workdir / f"attempt-{number}"
        subdir.mkdir()
        path = attempt(budget, subdir)
        size = None if path is None else path.stat().st_size
        if path is not None and size is not None and size <= limit:
            return path
        log.info("уровень бюджета %d: размер %s байт сверх лимита %d", number, size, limit)
        shutil.rmtree(subdir, ignore_errors=True)  # освободить диск до следующей попытки
    raise MediaTooLarge(f"все {len(BUDGETS)} уровня бюджета дали файл больше {limit} байт")


def ensure_fits(path: Path, *, limit: int = SEND_LIMIT_BYTES) -> Path:
    """Проверка факта после загрузки там, где бюджета в селекторе нет (Instagram)."""
    size = path.stat().st_size
    if size > limit:
        raise MediaTooLarge(f"файл {size} байт больше лимита {limit}")
    return path


# --- cookies (решение 7) ---------------------------------------------------------------------

SESSION_COOKIE = "sessionid"


def cookie_names(path: Path) -> set[str]:
    """Имена cookies из файла Netscape; значения не читаются (ни в память, ни в лог)."""
    names: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.removeprefix("#HttpOnly_").rstrip("\r\n")
                if line.startswith("#"):
                    continue
                fields = line.split("\t")
                if len(fields) >= 7:
                    names.add(fields[5])
    except OSError as exc:
        log.warning("не удалось прочитать cookies: %s", type(exc).__name__)
    return names


@dataclass(frozen=True, slots=True)
class CookiesCopy:
    """Копия cookies во временном каталоге загрузки; `names_before` — имена до загрузки."""

    path: Path
    names_before: frozenset[str] = field(repr=False)

    def session_lost(self) -> bool:
        """`sessionid` был в копии до загрузки и исчез после (yt-dlp удалил его при сохранении)."""
        return SESSION_COOKIE in self.names_before and SESSION_COOKIE not in cookie_names(self.path)


def copy_cookies(source: Path | None, workdir: Path) -> CookiesCopy | None:
    """Копия `source` в `workdir` с правами 600; исходник не трогается. Нет источника — None."""
    if source is None:
        return None
    target = workdir / "cookies.txt"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out, source.open("rb") as src:
        shutil.copyfileobj(src, out)
    return CookiesCopy(target, frozenset(cookie_names(target)))


# --- перевод ошибок ---------------------------------------------------------------------------


def describe(exc: BaseException) -> str:
    """Причина для лога: тип и текст (обрезан); значений cookies в тексте yt-dlp нет."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:300]}"


def _http_status(exc: BaseException) -> int | None:
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        status = getattr(node, "status", None)
        if isinstance(status, int):
            return status
        info = getattr(node, "exc_info", None)
        inner = info[1] if isinstance(info, tuple) and len(info) > 1 else None
        node = inner or getattr(node, "cause", None) or node.__cause__
    return None


def translate_error(exc: BaseException, *, session_lost: bool = False) -> DownloadError:
    """Ошибка yt-dlp → ошибка порта. Тексты — из проб (notes, пробы 4–5), статус 429 — по полю.

    «Cookies отвергнуты» — не по тексту, а по `session_lost` (решение 7): «Failed to parse JSON»
    даёт и смена формата Instagram. Незнакомое — `DownloadFailed`.
    """
    text = str(exc)
    reason = describe(exc)
    if _http_status(exc) == 429:
        return RateLimited(reason)
    if "Instagram sent an empty media response" in text:
        return LoginRequired(reason)
    if "Failed to parse JSON" in text:
        return LoginRequired(reason) if session_lost else DownloadFailed(reason)
    if "This video is unavailable" in text:
        return MediaNotFound(reason)
    return DownloadFailed(reason)


@contextmanager
def translating_errors(cookies: CookiesCopy | None = None) -> Iterator[None]:
    """Граница yt-dlp: его ошибки и ошибки файлов → ошибки порта. `Cancelled` и свои — как есть."""
    try:
        yield
    except (DownloadError, Cancelled):
        raise
    except (YoutubeDLError, OSError) as exc:
        lost = cookies.session_lost() if cookies is not None else False
        error = translate_error(exc, session_lost=lost)
        log.warning("ошибка загрузки → %s (%s)", type(error).__name__, error)
        raise error from exc


# --- опции yt-dlp -----------------------------------------------------------------------------


class _YdlLogger:
    """Вывод yt-dlp — в logging; отладочный вывод не нужен."""

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)


def base_options(cancel: threading.Event, *, cookies: CookiesCopy | None = None) -> dict[str, Any]:
    """Общие опции: сокет-тайм-аут (решение 8), хук отмены, страховка `max_filesize`, cookies."""
    options: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "logger": _YdlLogger(),
        "socket_timeout": SOCKET_TIMEOUT,
        "progress_hooks": [progress_hook(cancel)],
        "max_filesize": SEND_LIMIT_BYTES,
    }
    if cookies is not None:
        options["cookiefile"] = str(cookies.path)
    return options


# --- ffprobe (решение 5: метаданные — из файла, не из info dict) -----------------------------


@dataclass(frozen=True, slots=True)
class MediaProbe:
    width: int | None
    height: int | None
    duration: float | None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_probe(data: object) -> MediaProbe:
    """Разбор вывода `ffprobe -print_format json`; незнакомое и `None` → поля `None`."""
    if not isinstance(data, dict):
        return MediaProbe(None, None, None)
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
    rotations = [
        r.get("rotation")
        for r in video.get("side_data_list") or []
        if isinstance(r, dict) and "rotation" in r
    ]
    if rotations and isinstance(rotations[0], int | float) and abs(rotations[0]) % 180 == 90:
        width, height = height, width
    fmt = data.get("format")
    fmt = fmt if isinstance(fmt, dict) else {}
    duration = _positive_float(fmt.get("duration")) or _positive_float(video.get("duration"))
    return MediaProbe(width, height, duration)


def probe_media(
    path: Path, *, run: Callable[..., Any] = subprocess.run, timeout: float = 20.0
) -> MediaProbe:
    """ffprobe по файлу. Блокирующая (вызывать в потоке); сбой — `MediaProbe` из `None` и лог."""
    command = [
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
        str(path),
    ]  # fmt: skip
    try:
        done = run(command, capture_output=True, timeout=timeout, check=False)
        if done.returncode != 0:
            log.warning("ffprobe: код %s для %s", done.returncode, path.name)
            return MediaProbe(None, None, None)
        return parse_probe(json.loads(done.stdout))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.warning("ffprobe не сработал для %s: %s", path.name, describe(exc))
        return MediaProbe(None, None, None)
