"""FakeDownloader: ведёт себя как реальные адаптеры (контракт с ними — test_downloader_contract)."""

import asyncio

import pytest

from tests.fakes import FakeDownloader, FakeMedia, photo, video
from tg_bot.downloader import port
from tg_bot.downloader.port import (
    DownloadError,
    DownloadFailed,
    LoginRequired,
    MediaKind,
    MediaNotFound,
    MediaTooLarge,
    NoMedia,
    RateLimited,
)
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES
from tg_bot.links import Link, Platform, PostKind

LINK = Link(Platform.INSTAGRAM, PostKind.REEL, "X", "https://www.instagram.com/reel/X/")
ALL_ERRORS = [
    cls
    for cls in vars(port).values()
    if isinstance(cls, type) and issubclass(cls, DownloadError) and cls is not DownloadError
]


def download(fake, workdir):
    return asyncio.run(fake.download(LINK, workdir))


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def test_returns_items_in_order_inside_workdir(workdir):
    fake = FakeDownloader([photo("1.jpg"), video("2.mp4"), photo("3.jpg")], caption="подпись")
    result = download(fake, workdir)
    assert [i.path.name for i in result.items] == ["1.jpg", "2.mp4", "3.jpg"]
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.PHOTO]
    assert all(i.path.parent == workdir and i.path.is_file() for i in result.items)
    assert result.caption == "подпись"
    assert fake.calls == [(LINK, workdir)]


def test_metadata_and_size_are_as_given(workdir):
    (item,) = download(
        FakeDownloader([video("v.mp4", 5_000, width=1080, height=1920, duration=3.5)]), workdir
    ).items
    assert (item.width, item.height, item.duration) == (1080, 1920, 3.5)
    assert item.path.stat().st_size == 5_000


@pytest.mark.parametrize("cls", ALL_ERRORS)
def test_given_error_is_raised_as_is_and_nothing_is_written(workdir, cls):
    error = cls("причина")
    with pytest.raises(cls) as caught:
        download(FakeDownloader([video()], error=error), workdir)
    assert caught.value is error
    assert list(workdir.iterdir()) == []


def test_only_port_errors_are_allowed():
    """Реальные адаптеры наружу выбрасывают только `DownloadError`; фейк чужое не принимает."""
    with pytest.raises(TypeError):
        FakeDownloader(error=RuntimeError("x"))  # type: ignore[arg-type]


def test_empty_result_is_no_media(workdir):
    with pytest.raises(NoMedia):
        download(FakeDownloader([]), workdir)


@pytest.mark.parametrize(
    "media", [video(size=SEND_LIMIT_BYTES + 1), photo(size=SEND_LIMIT_BYTES + 1)]
)
def test_all_oversize_is_too_large_and_nothing_is_written(workdir, media):
    with pytest.raises(MediaTooLarge):
        download(FakeDownloader([media, media]), workdir)
    assert list(workdir.iterdir()) == []


@pytest.mark.parametrize(
    "media", [video("big.mp4", SEND_LIMIT_BYTES + 1), photo("big.jpg", SEND_LIMIT_BYTES + 1)]
)
def test_oversize_element_is_skipped_and_counted(workdir, media):
    result = download(FakeDownloader([photo("ok.jpg"), media]), workdir)
    assert [i.path.name for i in result.items] == ["ok.jpg"]
    assert result.skipped == 1 and result.owner_alert is None
    assert [p.name for p in workdir.iterdir()] == ["ok.jpg"]  # пропущенный не записан


def test_failed_elements_are_skipped_in_order_and_counted(workdir):
    fake = FakeDownloader(
        [
            video("1.mp4", error=DownloadFailed("x")),
            photo("2.jpg"),
            video("3.mp4"),
            photo("4.jpg", error=MediaNotFound("y")),
        ]
    )
    result = download(fake, workdir)
    assert [i.path.name for i in result.items] == ["2.jpg", "3.mp4"]
    assert result.skipped == 2 and result.owner_alert is None


def test_nothing_succeeded_raises_error_of_first_element(workdir):
    fake = FakeDownloader(
        [
            video("1.mp4", error=DownloadFailed("первая")),
            video("2.mp4", error=MediaNotFound("вторая")),
        ]
    )
    with pytest.raises(DownloadFailed, match="первая"):
        download(fake, workdir)
    assert list(workdir.iterdir()) == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [(RateLimited("429"), RateLimited), (LoginRequired("вход"), LoginRequired)],
)
def test_owner_alert_from_failed_element_while_others_succeed(workdir, error, expected):
    result = download(FakeDownloader([video("1.mp4", error=error), photo("2.jpg")]), workdir)
    assert result.skipped == 1 and result.owner_alert is expected


def test_session_lost_gives_owner_alert_and_login_required_when_nothing_succeeded(workdir):
    partial = FakeDownloader(
        [video("1.mp4", error=DownloadFailed("x")), photo("2.jpg")], session_lost=True
    )
    assert download(partial, workdir).owner_alert is LoginRequired
    none_left = FakeDownloader([video("1.mp4", error=DownloadFailed("x"))], session_lost=True)
    with pytest.raises(LoginRequired):
        download(none_left, workdir)
    kept = FakeDownloader([video("1.mp4", error=DownloadFailed("x")), photo("2.jpg")])
    assert download(kept, workdir).owner_alert is None  # положительный контроль


def test_media_error_must_be_port_error():
    with pytest.raises(TypeError):
        video(error=RuntimeError("x"))  # type: ignore[arg-type]


def test_exactly_at_limit_is_accepted(workdir):
    (item,) = download(FakeDownloader([video(size=SEND_LIMIT_BYTES)]), workdir).items
    assert item.path.stat().st_size == SEND_LIMIT_BYTES


def test_missing_workdir_is_download_failed(tmp_path):
    with pytest.raises(DownloadFailed):
        download(FakeDownloader([video()]), tmp_path / "nope")


@pytest.mark.parametrize("name", ["../escape.mp4", "sub/x.mp4", "/abs.mp4", "", ".."])
def test_writes_only_inside_workdir(tmp_path, workdir, name):
    with pytest.raises(ValueError):
        download(FakeDownloader([FakeMedia(MediaKind.VIDEO, name)]), workdir)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_gate_holds_download_until_released(workdir):
    async def scenario():
        gate = asyncio.Event()
        fake = FakeDownloader([video()], gate=gate)
        task = asyncio.create_task(fake.download(LINK, workdir))
        await fake.started.wait()
        for _ in range(10):
            await asyncio.sleep(0)
        assert not task.done() and list(workdir.iterdir()) == []
        gate.set()
        return await task

    assert len(asyncio.run(scenario()).items) == 1


def test_cancellation_while_gated_propagates_and_fake_writes_nothing(workdir):
    # Реальный адаптер при отмене может оставить в workdir `cookies.txt` и частичные файлы; фейк —
    # нет (см. docstring `tests/fakes.py`): тест проверяет только то, что делает фейк.
    async def scenario():
        fake = FakeDownloader([video()], gate=asyncio.Event())
        task = asyncio.create_task(fake.download(LINK, workdir))
        await fake.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return fake

    fake = asyncio.run(scenario())
    assert fake.cancelled is True
    assert list(workdir.iterdir()) == []


def test_not_found_example_is_user_facing(workdir):
    with pytest.raises(MediaNotFound) as caught:
        download(FakeDownloader(error=MediaNotFound("нет")), workdir)
    assert caught.value.user_message.strip()
