"""ThreadRunner (решение 8, «изменено в части B»): тайм-аут, флаг отмены, ожидание потока.

Без реального yt-dlp и без sleep: блокирующая функция ждёт `threading.Event`, тест ждёт
**состояния** (поток стартовал, флаг выставлен, поток завершился), а не времени. Числа вроде 0.05
— тайм-аут/порог, переданные параметром; 5–10 с — только предохранитель от зависшего теста.
"""

import asyncio
import logging
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tg_bot.downloader.port import DownloadFailed, DownloadTimeout
from tg_bot.downloader.ytdlp import Cancelled, ThreadRunner

SAFETY = 10  # с; предохранитель, нормальный ход теста его не использует
TIMEOUT = 0.05


class Blocking:
    """Фейковая блокирующая функция.

    `honors_flag=False` — «извлечение»: ждёт `release` и флаг игнорирует (хук там не вызывается).
    `honors_flag=True` — «загрузка»: просыпается по флагу, как progress-hook, и бросает `Cancelled`.
    """

    def __init__(self, *, honors_flag: bool, write_to: Path | None = None) -> None:
        self.honors_flag = honors_flag
        self.write_to = write_to
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.cancel: threading.Event | None = None
        self.saw_flag: bool | None = None
        self.wrote: bool | None = None
        self.thread_name: str | None = None

    def __call__(self, cancel: threading.Event) -> str:
        self.cancel = cancel
        self.thread_name = threading.current_thread().name
        self.started.set()
        try:
            if self.honors_flag:
                cancel.wait(SAFETY)
                self.saw_flag = cancel.is_set()
                if self.saw_flag:
                    raise Cancelled
            else:
                self.release.wait(SAFETY)
                self.saw_flag = cancel.is_set()
                if self.write_to is not None:
                    try:
                        self.write_to.write_text("данные")
                        self.wrote = True
                    except OSError:
                        self.wrote = False
            return "done"
        finally:
            self.finished.set()


class CountingExecutor(ThreadPoolExecutor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submitted = 0

    def submit(self, *args, **kwargs):
        self.submitted += 1
        return super().submit(*args, **kwargs)


class Watch(logging.Handler):
    """Ждёт запись нужного уровня как состояние (без sleep)."""

    def __init__(self, level: int) -> None:
        super().__init__(level)
        self.seen = threading.Event()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.seen.set()


@pytest.fixture
def watch():
    logger = logging.getLogger("tg_bot.downloader.ytdlp")
    handler = Watch(logging.WARNING)
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


async def happened(event: threading.Event) -> bool:
    return await asyncio.to_thread(event.wait, SAFETY)


async def settle() -> None:
    """Даёт циклу событий обработать готовое (без ожидания времени)."""
    for _ in range(30):
        await asyncio.sleep(0)


def test_timeout_waits_for_thread_that_ignores_flag():
    """Тайм-аут на извлечении: флаг есть, но DownloadTimeout — только после конца потока, а
    каталог (его удаляет вызывающий в finally) жив, пока поток может в него писать."""

    async def scenario() -> None:
        runner = ThreadRunner(limit=2, timeout=TIMEOUT, grace=SAFETY)
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "work"
            workdir.mkdir()
            fn = Blocking(honors_flag=False, write_to=workdir / "out.bin")

            async def caller() -> str:
                try:  # как pipeline: каталог принадлежит вызывающему
                    return await runner.run(fn)
                finally:
                    shutil.rmtree(workdir)

            task = asyncio.create_task(caller())
            assert await happened(fn.started)
            assert await happened(fn.cancel)  # тайм-аут сработал → флаг выставлен
            await settle()
            assert not task.done()  # DownloadTimeout ещё не выброшен
            assert not fn.finished.is_set()  # поток жив
            assert workdir.exists()  # каталог не удалён
            fn.release.set()
            with pytest.raises(DownloadTimeout):
                await task
            assert fn.finished.is_set()  # к моменту ошибки поток завершён
            assert fn.saw_flag is True
            assert fn.wrote is True  # писать было куда
            assert not workdir.exists()  # и только теперь каталог удалён
        runner.close()

    asyncio.run(scenario())


def test_semaphore_is_held_until_thread_finishes():
    """Семафор отпускается после завершения потока, а не после тайм-аута ожидания: пока поток
    жив, вторая загрузка даже не отправляется в пул."""

    async def scenario() -> None:
        executor = CountingExecutor(max_workers=2)
        runner = ThreadRunner(limit=1, timeout=TIMEOUT, grace=SAFETY, executor=executor)
        first = Blocking(honors_flag=False)
        second = Blocking(honors_flag=False)
        second.release.set()
        task1 = asyncio.create_task(runner.run(first))
        assert await happened(first.started)
        assert await happened(first.cancel)  # тайм-аут первой уже сработал
        task2 = asyncio.create_task(runner.run(second))
        await settle()
        assert executor.submitted == 1  # вторая ждёт семафор
        assert not second.started.is_set()
        first.release.set()
        with pytest.raises(DownloadTimeout):
            await task1
        assert await happened(second.started)  # семафор свободен → вторая стартовала
        assert await task2 == "done"
        assert executor.submitted == 2
        runner.close()

    asyncio.run(scenario())


def test_runner_uses_its_own_pool_not_default():
    """Положительный контроль к пулу: задачи уходят в переданный пул; пул по умолчанию (где
    `asyncio.to_thread`) не используется."""

    async def scenario() -> None:
        executor = CountingExecutor(max_workers=1)
        runner = ThreadRunner(limit=1, executor=executor)
        fn = Blocking(honors_flag=False)
        fn.release.set()
        assert await runner.run(fn) == "done"
        assert executor.submitted == 1
        runner.close()

    asyncio.run(scenario())


def test_default_pool_is_named_and_not_asyncio_default():
    async def scenario() -> None:
        runner = ThreadRunner(limit=2)
        fn = Blocking(honors_flag=False)
        fn.release.set()
        await runner.run(fn)
        assert fn.thread_name is not None and fn.thread_name.startswith("ytdlp")
        runner.close()

    asyncio.run(scenario())


def test_timeout_interrupts_download_phase_by_itself():
    """Загрузка: функция проверяет флаг (как progress-hook) → завершается сама, ждать нечего."""

    async def scenario() -> None:
        runner = ThreadRunner(limit=2, timeout=TIMEOUT, grace=SAFETY)
        fn = Blocking(honors_flag=True)
        with pytest.raises(DownloadTimeout):
            await runner.run(fn)
        assert fn.saw_flag is True
        assert fn.finished.is_set()
        runner.close()

    asyncio.run(scenario())


def test_result_and_errors_pass_through_without_timeout():
    """Положительный контроль: без тайм-аута результат и ошибки функции доходят как есть."""

    async def scenario() -> None:
        runner = ThreadRunner(limit=2, timeout=SAFETY)

        def ok(cancel: threading.Event) -> int:
            assert not cancel.is_set()
            return 42

        def fail(cancel: threading.Event) -> int:
            raise DownloadFailed("причина")

        assert await runner.run(ok) == 42
        with pytest.raises(DownloadFailed, match="причина"):
            await runner.run(fail)
        runner.close()

    asyncio.run(scenario())


def test_warning_when_thread_outlives_grace(watch):
    async def scenario() -> None:
        runner = ThreadRunner(limit=2, timeout=TIMEOUT, grace=TIMEOUT)
        fn = Blocking(honors_flag=False)
        task = asyncio.create_task(runner.run(fn))
        assert await happened(watch.seen)  # WARNING появился, пока поток жив
        assert not fn.finished.is_set()
        assert not task.done()  # ожидание продолжается
        fn.release.set()
        with pytest.raises(DownloadTimeout):
            await task
        runner.close()

    asyncio.run(scenario())
    assert [r.levelno for r in watch.records] == [logging.WARNING]
    assert "сек" in watch.records[0].getMessage()


def test_no_warning_when_thread_ends_within_grace(watch):
    async def scenario() -> None:
        runner = ThreadRunner(limit=2, timeout=TIMEOUT, grace=SAFETY)
        with pytest.raises(DownloadTimeout):
            await runner.run(Blocking(honors_flag=True))
        runner.close()

    asyncio.run(scenario())
    assert watch.records == []


def test_cancelled_error_propagates_and_logs_error_for_stuck_thread(watch):
    async def scenario() -> Blocking:
        runner = ThreadRunner(limit=2, timeout=SAFETY, grace=TIMEOUT)
        fn = Blocking(honors_flag=False)
        task = asyncio.create_task(runner.run(fn))
        assert await happened(fn.started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task  # пробрасывается, а не глотается
        assert fn.cancel is not None and fn.cancel.is_set()  # флаг выставлен
        assert not fn.finished.is_set()  # поток не успел за порог
        fn.release.set()
        runner.close()
        return fn

    asyncio.run(scenario())
    assert [r.levelno for r in watch.records] == [logging.ERROR]


def test_cancelled_error_without_error_log_when_thread_stops_in_time(watch):
    """Положительный контроль: поток, видящий флаг, успевает — CancelledError тот же, ERROR нет."""

    async def scenario() -> Blocking:
        runner = ThreadRunner(limit=2, timeout=SAFETY, grace=SAFETY)
        fn = Blocking(honors_flag=True)
        task = asyncio.create_task(runner.run(fn))
        assert await happened(fn.started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fn.finished.is_set()
        runner.close()
        return fn

    asyncio.run(scenario())
    assert watch.records == []
