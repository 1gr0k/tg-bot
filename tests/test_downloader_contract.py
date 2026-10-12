"""Контракт порта: FakeDownloader и реальные адаптеры (на фейковом yt-dlp) ведут себя одинаково.

Одни и те же сценарии идут через все реализации, где они имеют смысл; общие проверки — типы
ошибок, порядок, файлы только внутри `workdir`, непустой результат. Это и есть «сверка фейка с
реализацией» (решение 20, задача части B). Частично удачная карусель и сигнал владельцу
(`owner_alert`) — тоже общий контракт фейка и `InstagramDownloader` (ревью части B (2)).
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
    DownloadFailed,
    DownloadResult,
    LoginRequired,
    MediaKind,
    MediaNotFound,
    MediaTooLarge,
    NoMedia,
    RateLimited,
)
from tg_bot.downloader.shorts import ShortsDownloader
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES, ThreadRunner
from tg_bot.links import Link, Platform, PostKind

REEL = load_fixture("instagram_reel")
MIXED = load_fixture("instagram_mixed_carousel")
SHORTS = load_fixture("youtube_shorts_long")
IG_LINK = Link(Platform.INSTAGRAM, PostKind.POST, "X", "https://www.instagram.com/p/X/")
YT_LINK = Link(Platform.YOUTUBE, PostKind.SHORTS, "X", "https://www.youtube.com/shorts/X")
VIDEO_ID = MIXED["entries"][1]["id"]  # MIXED: фото, затем видео

_EMPTY = "ERROR: [Instagram] X: Instagram sent an empty media response."
_UNAVAILABLE = "ERROR: [youtube] X: This video is unavailable"
_PARSE_JSON = (
    "ERROR: [Instagram] X: Failed to parse JSON (caused by JSONDecodeError()); please report"
)
BIG = SEND_LIMIT_BYTES + 1


class _TooManyRequests(YtDownloadError):
    status = 429


def _fake(*args, **kwargs):
    def build():
        return FakeDownloader(*args, **kwargs), IG_LINK, None

    return build


def _instagram(script: Script, cookies: Path | None = None):
    def build():
        runner = ThreadRunner(limit=1, timeout=30, grace=30)
        return (
            InstagramDownloader(runner, cookies, ydl_factory=script.factory, probe=probe_by_name()),
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

# Частично удачная карусель (фото + видео): один элемент пропущен, второй доставлен. Что видит
# вызывающий — оставшиеся элементы, `skipped` и сигнал владельцу — у фейка и адаптера одно и то же.
PARTIAL = {
    "video over limit": (
        None,
        [MediaKind.PHOTO],
        [
            ("fake", _fake([photo("1.jpg"), video("2.mp4", size=BIG)])),
            ("instagram", _instagram(Script(MIXED, video_size=BIG))),
        ],
    ),
    "photo failed": (
        None,
        [MediaKind.VIDEO],
        [
            ("fake", _fake([photo("1.jpg", error=DownloadFailed("x")), video("2.mp4")])),
            ("instagram", _instagram(Script(MIXED, photo_error=YtDownloadError("HTTP Error 403")))),
        ],
    ),
    "video rate limited": (
        RateLimited,
        [MediaKind.PHOTO],
        [
            ("fake", _fake([photo("1.jpg"), video("2.mp4", error=RateLimited("429"))])),
            (
                "instagram",
                _instagram(Script(MIXED, entry_errors={VIDEO_ID: _TooManyRequests("HTTP 429")})),
            ),
        ],
    ),
    "video login required": (
        LoginRequired,
        [MediaKind.PHOTO],
        [
            ("fake", _fake([photo("1.jpg"), video("2.mp4", error=LoginRequired("вход"))])),
            (
                "instagram",
                _instagram(Script(MIXED, entry_errors={VIDEO_ID: YtDownloadError(_EMPTY)})),
            ),
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
    assert result.skipped == 0 and result.owner_alert is None  # чистая загрузка — без сигналов
    for item in result.items:
        assert item.path.is_file() and item.path.stat().st_size > 0
        assert workdir in item.path.parents  # внутри workdir
    assert all(workdir in p.parents for p in _files(root))  # и больше нигде


@pytest.mark.parametrize(
    ("scenario", "backend", "build", "alert", "kinds"),
    [
        pytest.param(s, e[0], e[1], alert, kinds, id=f"{s}-{e[0]}")
        for s, (alert, kinds, entries) in PARTIAL.items()
        for e in entries
    ],
)
def test_partial_carousel_contract(sandbox, scenario, backend, build, alert, kinds):
    root, workdir = sandbox
    result = _run(build, workdir)
    assert [i.kind for i in result.items] == kinds
    assert result.skipped == 1  # «не удалось N из M»: N = skipped, M = len(items) + skipped
    assert result.owner_alert is alert
    assert all(workdir in p.parents for p in _files(root))
    assert {p.name for p in _files(root)} == {i.path.name for i in result.items}  # пропущенного нет


# --- потеря сессии: yt-dlp стирает `sessionid` в `close()` (только фейк и Instagram) ----------


def _cookies_file(directory: Path) -> Path:
    lines = [
        "\t".join([".instagram.com", "TRUE", "/", "TRUE", "1999999999", name, "v"])
        for name in ("csrftoken", "sessionid", "mid")
    ]
    path = directory / "instagram.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_SESSION_LOST = "\t".join([".instagram.com", "TRUE", "/", "TRUE", "1999999999", "mid", "v"]) + "\n"


def _lost_fake(photo_fails: bool, video_fails: bool):
    media = [
        photo("1.jpg", error=DownloadFailed("x") if photo_fails else None),
        video("2.mp4", error=DownloadFailed("x") if video_fails else None),
    ]
    return lambda cookies: _fake(media, session_lost=True)()


def _lost_instagram(photo_fails: bool, video_fails: bool):
    def factory(cookies: Path):
        script = Script(
            MIXED,
            photo_error=YtDownloadError(_PARSE_JSON) if photo_fails else None,
            entry_errors={VIDEO_ID: YtDownloadError(_PARSE_JSON)} if video_fails else None,
            cookies_after=_SESSION_LOST,
        )
        return _instagram(script, cookies)()

    return factory


LOST = {"fake": _lost_fake, "instagram": _lost_instagram}


@pytest.mark.parametrize("backend", LOST)
def test_session_lost_with_one_of_two_gives_owner_alert(sandbox, tmp_path_factory, backend):
    _, workdir = sandbox
    cookies = _cookies_file(tmp_path_factory.mktemp("cookies"))
    factory = LOST[backend](False, True)
    result = _run(lambda: factory(cookies), workdir)
    assert [i.kind for i in result.items] == [MediaKind.PHOTO]
    assert result.skipped == 1 and result.owner_alert is LoginRequired


@pytest.mark.parametrize("backend", LOST)
def test_session_lost_with_zero_of_two_is_login_required(sandbox, tmp_path_factory, backend):
    _, workdir = sandbox
    cookies = _cookies_file(tmp_path_factory.mktemp("cookies"))
    factory = LOST[backend](True, True)
    with pytest.raises(LoginRequired):
        _run(lambda: factory(cookies), workdir)


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
