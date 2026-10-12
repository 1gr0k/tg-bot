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
