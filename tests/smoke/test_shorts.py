"""Дымовые тесты ShortsDownloader: реальный YouTube, реальные yt-dlp/ffmpeg/ffprobe.

Запуск вручную: `uv run pytest -m smoke tests/smoke/test_shorts.py`. Cookies не нужны.
"""

import pytest

from tests.smoke._helpers import (
    SHORTS_LONG,
    SHORTS_MISSING,
    SHORTS_SHORT,
    codecs,
    download_shorts,
)
from tg_bot.downloader.port import MediaKind, MediaNotFound
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES

pytestmark = pytest.mark.smoke


def test_long_shorts_fits_telegram_limit_and_is_h264_aac(tmp_path):
    """135 с в 1080p — 58 440 000 байт (проба 5): бюджет размера должен свести к лимиту."""
    result = download_shorts(SHORTS_LONG, tmp_path)
    (item,) = result.items
    assert item.kind is MediaKind.VIDEO
    assert item.path.stat().st_size <= SEND_LIMIT_BYTES
    assert tmp_path in item.path.parents
    assert codecs(item.path) == {"video": "h264", "audio": "aac"}
    assert item.width and item.height and item.height > item.width  # вертикальное видео
    assert item.duration == pytest.approx(135, abs=3)
    assert result.caption.strip()  # описание или название


def test_short_shorts_is_downloaded_with_metadata(tmp_path):
    result = download_shorts(SHORTS_SHORT, tmp_path)
    (item,) = result.items
    assert item.path.stat().st_size <= SEND_LIMIT_BYTES
    assert codecs(item.path)["video"] == "h264"
    assert item.width and item.height and item.duration


def test_missing_video_is_media_not_found(tmp_path):
    with pytest.raises(MediaNotFound):
        download_shorts(SHORTS_MISSING, tmp_path)
