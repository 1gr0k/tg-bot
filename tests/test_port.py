"""Порт загрузчика: инварианты структур и иерархия ошибок."""

import ast
import dataclasses
from pathlib import Path

import pytest

from tg_bot.downloader import port
from tg_bot.downloader.port import (
    DownloadError,
    DownloadResult,
    MediaItem,
    MediaKind,
)

_ERRORS = [
    cls
    for cls in vars(port).values()
    if isinstance(cls, type) and issubclass(cls, DownloadError) and cls is not DownloadError
]


def _item(name: str = "a.mp4") -> MediaItem:
    return MediaItem(MediaKind.VIDEO, Path(name))


def test_result_requires_items():
    with pytest.raises(ValueError):
        DownloadResult(items=())


def test_result_keeps_order_and_defaults():
    result = DownloadResult(items=(_item("1.mp4"), _item("2.mp4")))
    assert [i.path.name for i in result.items] == ["1.mp4", "2.mp4"]
    assert result.caption == ""


def test_owner_alert_defaults_to_none_and_accepts_only_login_or_rate_limit():
    assert DownloadResult(items=(_item(),)).owner_alert is None
    for cls in (port.LoginRequired, port.RateLimited):
        assert DownloadResult(items=(_item(),), owner_alert=cls).owner_alert is cls
    for bad in (port.MediaTooLarge, port.DownloadError, port.LoginRequired("x"), "LoginRequired"):
        with pytest.raises(ValueError):
            DownloadResult(items=(_item(),), owner_alert=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("errors", "lost", "expected"),
    [
        ([], False, None),
        ([port.MediaTooLarge("x"), port.DownloadFailed("y")], False, None),
        ([port.RateLimited("x")], False, port.RateLimited),
        ([port.LoginRequired("x")], False, port.LoginRequired),
        ([port.RateLimited("x"), port.LoginRequired("y")], False, port.LoginRequired),
        ([port.LoginRequired("y"), port.RateLimited("x")], False, port.LoginRequired),
        ([], True, port.LoginRequired),
        ([port.RateLimited("x")], True, port.LoginRequired),
    ],
)
def test_owner_alert_of(errors, lost, expected):
    assert port.owner_alert_of(errors, session_lost=lost) is expected


def test_result_skipped_defaults_to_zero_and_is_not_negative():
    assert DownloadResult(items=(_item(),)).skipped == 0
    assert DownloadResult(items=(_item(),), skipped=2).skipped == 2
    with pytest.raises(ValueError):
        DownloadResult(items=(_item(),), skipped=-1)


def test_structures_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        _item().width = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        DownloadResult(items=(_item(),)).caption = "x"  # type: ignore[misc]


def test_error_hierarchy_is_complete():
    assert {c.__name__ for c in _ERRORS} == {
        "MediaNotFound",
        "LoginRequired",
        "RateLimited",
        "MediaTooLarge",
        "DownloadTimeout",
        "NoMedia",
        "DownloadFailed",
    }


@pytest.mark.parametrize("cls", [DownloadError, *_ERRORS])
def test_every_error_has_own_user_message(cls):
    assert cls.user_message.strip()
    assert "yt-dlp" not in cls.user_message


def test_user_messages_are_distinct():
    messages = [c.user_message for c in _ERRORS]
    assert len(set(messages)) == len(messages)


def test_reason_for_log_is_str_of_error():
    assert str(port.DownloadFailed("причина для лога")) == "причина для лога"


def test_port_does_not_import_ytdlp_or_aiogram():
    """Порт — граница слоёв (решение 4): никаких типов yt-dlp и aiogram."""
    tree = ast.parse(Path(port.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name).split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [None])
        if (n.module if isinstance(n, ast.ImportFrom) else a.name)
    }
    assert imported  # положительный контроль: разбор импортов что-то нашёл
    assert not imported & {"yt_dlp", "aiogram"}
