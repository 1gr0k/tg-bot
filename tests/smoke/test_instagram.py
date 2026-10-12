"""Дымовые тесты InstagramDownloader: реальный Instagram, реальные yt-dlp/ffprobe.

Запуск вручную: `uv run pytest -m smoke tests/smoke/test_instagram.py`.
- Класс `TestWithoutCookies` — без cookies (Mac/VPN); запускает агент.
- Класс `TestWithCookies` — нужен `IG_COOKIES_FILE` (путь к файлу cookies Netscape); без него тесты
  пропускаются. Запускает владелец. Значения cookies не читаются и не печатаются; испорченный
  `sessionid` готовится во временной копии (исходный файл не меняется, сравнение — по sha256);
  каталоги с копиями cookies — свой временный каталог, удаляемый в `finally`.
"""

import pytest

from tests.smoke._helpers import (
    MIXED_CAROUSEL,
    PHOTO_POST,
    REEL,
    REEL_VIA_P,
    UNAVAILABLE_WITHOUT_LOGIN,
    codecs,
    download_instagram,
    file_sha256,
    private_dir,
    require_cookies,
    write_broken_sessionid,
)
from tg_bot.downloader.instagram import MIN_PHOTO_SIDE
from tg_bot.downloader.port import LoginRequired, MediaKind
from tg_bot.downloader.ytdlp import SEND_LIMIT_BYTES

pytestmark = pytest.mark.smoke


def _check_reel(result, tmp_path):
    (item,) = result.items
    assert item.kind is MediaKind.VIDEO
    assert tmp_path in item.path.parents
    assert item.path.stat().st_size <= SEND_LIMIT_BYTES
    assert codecs(item.path) == {"video": "h264", "audio": "aac"}  # решение 5, не VP9
    assert (item.width, item.height) == (720, 1280)
    assert item.duration == pytest.approx(73, abs=2)
    assert result.caption.strip()


def _check_photo(result, tmp_path):
    (item,) = result.items
    assert item.kind is MediaKind.PHOTO
    assert tmp_path in item.path.parents
    # размер картинки, а не только факт загрузки (решение 6): не превью, а оригинал
    assert item.width and item.height
    assert min(item.width, item.height) >= MIN_PHOTO_SIDE
    assert item.path.stat().st_size > 100_000


def _check_mixed(result, tmp_path):
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.VIDEO]
    photo, video = result.items
    assert min(photo.width, photo.height) >= MIN_PHOTO_SIDE
    assert video.width and video.height and video.duration
    assert all(tmp_path in i.path.parents for i in result.items)
    assert result.caption.strip()


class TestWithoutCookies:
    def test_reel(self, tmp_path):
        _check_reel(download_instagram(REEL, tmp_path), tmp_path)

    def test_p_link_with_single_video_is_one_video(self, tmp_path):
        """`/p/<код>` с одиночным видео = тот же Reel (notes): тип определяет ответ yt-dlp."""
        _check_reel(download_instagram(REEL_VIA_P, tmp_path), tmp_path)

    def test_photo_post_is_original_not_preview(self, tmp_path):
        _check_photo(download_instagram(PHOTO_POST, tmp_path), tmp_path)

    def test_mixed_carousel(self, tmp_path):
        _check_mixed(download_instagram(MIXED_CAROUSEL, tmp_path), tmp_path)

    def test_post_unavailable_without_login(self, tmp_path):
        with pytest.raises(LoginRequired):
            download_instagram(UNAVAILABLE_WITHOUT_LOGIN, tmp_path)


class TestWithCookies:
    """Каталоги загрузки — только внутри `private_dir()` (копия cookies удаляется в `finally`)."""

    def test_reel_and_p_link(self):
        cookies = require_cookies()
        with private_dir() as root:
            for name, url in (("reel", REEL), ("p", REEL_VIA_P)):
                workdir = root / name
                workdir.mkdir()
                _check_reel(download_instagram(url, workdir, cookies), workdir)

    def test_photo_post_and_mixed_carousel(self):
        cookies = require_cookies()
        with private_dir() as root:
            photo_dir, mixed_dir = root / "photo", root / "mixed"
            photo_dir.mkdir()
            mixed_dir.mkdir()
            _check_photo(download_instagram(PHOTO_POST, photo_dir, cookies), photo_dir)
            _check_mixed(download_instagram(MIXED_CAROUSEL, mixed_dir, cookies), mixed_dir)

    def test_source_cookies_file_is_not_modified(self):
        """Решение 7: yt-dlp получает копию; исходный файл остаётся тем же (sha256 до и после)."""
        cookies = require_cookies()
        before = file_sha256(cookies)
        with private_dir() as root:
            workdir = root / "w"
            workdir.mkdir()
            download_instagram(REEL, workdir, cookies)
        assert file_sha256(cookies) == before

    def test_broken_sessionid_is_login_required(self):
        """Аналог `--mode broken` из пробы 4: значение `sessionid` заменено — cookies отвергнуты."""
        source = require_cookies()
        with private_dir() as root:
            broken = root / "broken.txt"
            if not write_broken_sessionid(source, broken):
                pytest.skip("в файле cookies нет sessionid")
            workdir = root / "w"
            workdir.mkdir()
            with pytest.raises(LoginRequired):
                download_instagram(REEL, workdir, broken)
