"""Контракт порта: FakeDownloader и реальные адаптеры (на фейковом yt-dlp) ведут себя одинаково.

Одни и те же сценарии идут через все реализации, где они имеют смысл; общие проверки — типы
ошибок, порядок, файлы только внутри `workdir`, непустой результат. Это и есть «сверка фейка с
реализацией» (решение 20, задача части B).
"""

import asyncio
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError

from tests.fake_ytdlp import Script, load_fixture, probe_by_name
from tests.fakes import FakeDownloader, photo, video
from tg_bot.downloader.instagram import InstagramDownloader
from tg_bot.downloader.port import (
    DownloadError,
    DownloadResult,
    LoginRequired,
    MediaKind,
    MediaNotFound,
    MediaTooLarge,
    NoMedia,
)
from tg_bot.downloader.shorts import ShortsDownloader
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES, ThreadRunner
from tg_bot.links import Link, Platform, PostKind

REEL = load_fixture("instagram_reel")
MIXED = load_fixture("instagram_mixed_carousel")
SHORTS = load_fixture("youtube_shorts_long")
IG_LINK = Link(Platform.INSTAGRAM, PostKind.POST, "X", "https://www.instagram.com/p/X/")
YT_LINK = Link(Platform.YOUTUBE, PostKind.SHORTS, "X", "https://www.youtube.com/shorts/X")

_EMPTY = "ERROR: [Instagram] X: Instagram sent an empty media response."
_UNAVAILABLE = "ERROR: [youtube] X: This video is unavailable"
BIG = SEND_LIMIT_BYTES + 1


def _fake(*args, **kwargs):
    def build():
        return FakeDownloader(*args, **kwargs), IG_LINK, None

    return build


def _instagram(script: Script):
    def build():
        runner = ThreadRunner(limit=1, timeout=30, grace=30)
        return (
            InstagramDownloader(runner, ydl_factory=script.factory, probe=probe_by_name()),
            IG_LINK,
            runner,
        )

    return build


def _shorts(script: Script):
    def build():
        runner = ThreadRunner(limit=1, timeout=30, grace=30)
        return (
            ShortsDownloader(runner, ydl_factory=script.factory, probe=probe_by_name()),
            YT_LINK,
            runner,
        )

    return build


SUCCESS = {
    "one video": [
        ("fake", _fake([video("v.mp4")], "подпись"), [MediaKind.VIDEO]),
        ("instagram", _instagram(Script(REEL)), [MediaKind.VIDEO]),
        ("shorts", _shorts(Script(SHORTS, attempt_sizes=[1_000_000])), [MediaKind.VIDEO]),
    ],
    "photo and video carousel": [
        ("fake", _fake([photo("1.jpg"), video("2.mp4")]), [MediaKind.PHOTO, MediaKind.VIDEO]),
        ("instagram", _instagram(Script(MIXED)), [MediaKind.PHOTO, MediaKind.VIDEO]),
    ],
}

FAILURES = {
    "not found": (
        MediaNotFound,
        [
            ("fake", _fake(error=MediaNotFound("нет"))),
            ("shorts", _shorts(Script(error=YtDownloadError(_UNAVAILABLE)))),
        ],
    ),
    "login required": (
        LoginRequired,
        [
            ("fake", _fake(error=LoginRequired("вход"))),
            ("instagram", _instagram(Script(error=YtDownloadError(_EMPTY)))),
        ],
    ),
    "too large": (
        MediaTooLarge,
        [
            ("fake", _fake([video(size=BIG)])),
            ("instagram", _instagram(Script(REEL, video_size=BIG))),
            ("shorts", _shorts(Script(SHORTS, attempt_sizes=[BIG] * 3, ignore_max_filesize=True))),
        ],
    ),
    "no media": (
        NoMedia,
        [
            ("fake", _fake([])),
            ("instagram", _instagram(Script({"id": "X", "entries": []}))),
        ],
    ),
}


def _cases(table):
    for scenario, entries in table.items():
        for entry in entries:
            yield pytest.param(scenario, *entry, id=f"{scenario}-{entry[0]}")


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


def _run(build, workdir: Path):
    downloader, link, runner = build()
    try:
        return asyncio.run(downloader.download(link, workdir))
    finally:
        if runner is not None:
            runner.close()


@pytest.fixture
def sandbox(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    return tmp_path, workdir


@pytest.mark.parametrize(
    ("scenario", "backend", "build", "kinds"),
    [pytest.param(s, *e, id=f"{s}-{e[0]}") for s, entries in SUCCESS.items() for e in entries],
)
def test_success_contract(sandbox, scenario, backend, build, kinds):
    root, workdir = sandbox
    result = _run(build, workdir)
    assert isinstance(result, DownloadResult)
    assert isinstance(result.items, tuple) and result.items  # не пусто
    assert isinstance(result.caption, str)
    assert [i.kind for i in result.items] == kinds  # исходный порядок
    for item in result.items:
        assert item.path.is_file() and item.path.stat().st_size > 0
        assert workdir in item.path.parents  # внутри workdir
    assert all(workdir in p.parents for p in _files(root))  # и больше нигде


@pytest.mark.parametrize(
    ("scenario", "backend", "build", "expected"),
    [
        pytest.param(s, e[0], e[1], expected, id=f"{s}-{e[0]}")
        for s, (expected, entries) in FAILURES.items()
        for e in entries
    ],
)
def test_failure_contract(sandbox, scenario, backend, build, expected):
    root, workdir = sandbox
    with pytest.raises(DownloadError) as caught:
        _run(build, workdir)
    assert type(caught.value) is expected  # тот же тип ошибки у всех реализаций
    assert caught.value.user_message.strip()
    assert all(workdir in p.parents for p in _files(root))  # даже при ошибке — только workdir
