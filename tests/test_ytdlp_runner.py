"""ThreadRunner (решение 8, «изменено в части B»): тайм-аут, флаг отмены, ожидание потока.

Без реального yt-dlp и без sleep: блокирующая функция ждёт `threading.Event`, тест ждёт
**состояния** (поток стартовал, флаг выставлен, поток завершился), а не времени. Числа вроде 0.05
— тайм-аут/порог, переданные параметром; 5–10 с — только предохранитель от зависшего теста.
"""

import asyncio
import gc
import logging
import shutil
import tempfile
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from tg_bot.downloader.port import DownloadFailed, DownloadTimeout, MediaTooLarge
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


class Ends:
    """Функция, игнорирующая флаг до `release`, а потом завершающаяся заданным образом."""

    def __init__(self, *, raises: BaseException | None = None, result: str = "ok") -> None:
        self.raises = raises
        self.result = result
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel: threading.Event | None = None

    def __call__(self, cancel: threading.Event) -> str:
        self.cancel = cancel
        self.started.set()
        self.release.wait(SAFETY)
        if self.raises is not None:
            raise self.raises
        return self.result


async def _timed_out(runner: ThreadRunner, fn: Ends) -> BaseException:
    """Запускает `fn`, ждёт срабатывания тайм-аута, отпускает поток; возвращает ошибку `run`."""
    task = asyncio.create_task(runner.run(fn))
    assert await happened(fn.started)
    assert await happened(fn.cancel)  # флаг выставлен — тайм-аут сработал
    fn.release.set()
    with pytest.raises(BaseException) as caught:
        await task
    return caught.value


def test_thread_error_after_timeout_wins_over_timeout(watch):
    """M1: поток после тайм-аута закончился `DownloadError` — это и есть причина; WARNING с
    длительностью."""
    error = MediaTooLarge("файл больше лимита")

    async def scenario() -> BaseException:
        runner = ThreadRunner(limit=1, timeout=TIMEOUT, grace=SAFETY)
        try:
            return await _timed_out(runner, Ends(raises=error))
        finally:
            runner.close()

    assert asyncio.run(scenario()) is error
    assert [r.levelno for r in watch.records] == [logging.WARNING]
    message = watch.records[0].getMessage()
    assert "MediaTooLarge" in message and "сек" in message


@pytest.mark.parametrize(
    "raises",
    [Cancelled(), ValueError("своя ошибка"), TimeoutError("из потока")],
    ids=["Cancelled", "ValueError", "TimeoutError"],
)
def test_other_thread_outcome_after_timeout_is_timeout_from_it(watch, raises):
    async def scenario() -> BaseException:
        runner = ThreadRunner(limit=1, timeout=TIMEOUT, grace=SAFETY)
        try:
            return await _timed_out(runner, Ends(raises=raises))
        finally:
            runner.close()

    error = asyncio.run(scenario())
    assert type(error) is DownloadTimeout
    # asyncio копирует `TimeoutError` из потока в новый объект (тот же тип и аргументы)
    assert type(error.__cause__) is type(raises) and error.__cause__.args == raises.args
    assert watch.records == []  # WARNING только для ошибки порта


def test_thread_result_after_timeout_is_timeout_without_cause():
    async def scenario() -> BaseException:
        runner = ThreadRunner(limit=1, timeout=TIMEOUT, grace=SAFETY)
        try:
            return await _timed_out(runner, Ends(result="поздно"))
        finally:
            runner.close()

    error = asyncio.run(scenario())
    assert type(error) is DownloadTimeout and error.__cause__ is None


class InlineExecutor(ThreadPoolExecutor):
    """Выполняет функцию сразу в `submit`: `future` готов до того, как `run` начнёт ждать, а
    тайм-аут 0 срабатывает в той же итерации цикла — детерминированная «гонка» без потоков."""

    def submit(self, fn, /, *args, **kwargs):
        done: Future = Future()
        try:
            done.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 — перенос в future, как у ThreadPoolExecutor
            done.set_exception(exc)
        return done


def test_result_ready_when_timeout_fires_is_returned_not_dropped():
    """Тайм-аут сработал в той же итерации, где `future` завершился: готовый результат отдаётся,
    а не заменяется `DownloadTimeout` (решение 8.4: отбрасывается только пришедший ПОЗЖЕ)."""

    async def scenario() -> str:
        runner = ThreadRunner(limit=1, timeout=0, grace=SAFETY, executor=InlineExecutor(1))
        try:
            return await runner.run(lambda cancel: "готово")
        finally:
            runner.close()

    assert asyncio.run(scenario()) == "готово"


def test_error_ready_when_timeout_fires_is_the_thread_error():
    """Положительный контроль к предыдущему: готова ошибка порта — выбрасывается она."""

    def fail(cancel: threading.Event) -> str:
        raise MediaTooLarge("файл больше лимита")

    async def scenario() -> None:
        runner = ThreadRunner(limit=1, timeout=0, grace=SAFETY, executor=InlineExecutor(1))
        try:
            with pytest.raises(MediaTooLarge):
                await runner.run(fail)
        finally:
            runner.close()

    asyncio.run(scenario())


def test_own_timeout_error_of_function_is_not_a_download_timeout():
    """L2: тайм-аут определяет сам `asyncio.timeout` (expired), а не тип `TimeoutError`."""

    async def scenario() -> None:
        runner = ThreadRunner(limit=1, timeout=SAFETY)

        def fn(cancel: threading.Event) -> None:
            raise TimeoutError("сокет")

        try:
            with pytest.raises(TimeoutError, match="сокет") as caught:
                await runner.run(fn)
            assert type(caught.value) is TimeoutError  # не DownloadTimeout
        finally:
            runner.close()

    asyncio.run(scenario())


def test_semaphore_is_released_after_thread_error():
    """L3: ошибка потока отпускает семафор — вторая задача при `limit=1` стартует."""

    async def scenario() -> None:
        executor = CountingExecutor(max_workers=2)
        runner = ThreadRunner(limit=1, timeout=SAFETY, executor=executor)

        def fail(cancel: threading.Event) -> None:
            raise DownloadFailed("упала")

        second = Blocking(honors_flag=False)
        second.release.set()
        with pytest.raises(DownloadFailed):
            await runner.run(fail)
        # wait_for: при утечке семафора тест падает через 5 с, а не висит
        assert await asyncio.wait_for(runner.run(second), 5) == "done"
        assert executor.submitted == 2
        runner.close()

    asyncio.run(scenario())


def test_semaphore_is_released_after_cancelled_error():
    """L3: `CancelledError` (остановка) тоже отпускает семафор; вторая задача стартует."""

    async def scenario() -> None:
        runner = ThreadRunner(limit=1, timeout=SAFETY, grace=SAFETY)
        first = Blocking(honors_flag=True)
        task1 = asyncio.create_task(runner.run(first))
        assert await happened(first.started)
        second = Blocking(honors_flag=False)
        second.release.set()
        task2 = asyncio.create_task(runner.run(second))
        await settle()
        assert not second.started.is_set()  # ждёт семафор
        task1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task1
        assert await asyncio.wait_for(task2, 5) == "done"  # при утечке семафора — падение за 5 с
        assert second.started.is_set()
        runner.close()

    asyncio.run(scenario())


def test_thread_error_is_retrieved_even_when_run_no_longer_waits():
    """L4: поток, завершившийся ошибкой после остановки, не даёт «exception was never
    retrieved» — исключение забирает done-callback."""
    complaints: list[dict] = []

    async def scenario() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: complaints.append(ctx))
        runner = ThreadRunner(limit=1, timeout=SAFETY, grace=TIMEOUT)
        fn = Ends(raises=DownloadFailed("после остановки"))
        task = asyncio.create_task(runner.run(fn))
        assert await happened(fn.started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task  # `run` вышел, поток ещё жив
        del task
        fn.release.set()  # поток падает, когда ждать уже некому
        for _ in range(50):
            await asyncio.sleep(0.01)
            gc.collect()
        runner.close()

    asyncio.run(scenario())
    assert complaints == []


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
