"""Дымовые тесты InstagramDownloader: реальный Instagram, реальные yt-dlp/ffprobe.

Запуск вручную: `uv run pytest -m smoke tests/smoke/test_instagram.py`.
- Класс `TestWithoutCookies` — без cookies (Mac/VPN); запускает агент.
- Класс `TestWithCookies` — нужен `IG_COOKIES_FILE` (путь к файлу cookies Netscape); без него тесты
  пропускаются. Запускает владелец. Значения cookies не читаются и не печатаются; испорченный
  `sessionid` готовится во временной копии (исходный файл не меняется).
"""

import shutil

import pytest

from tests.smoke._helpers import (
    MIXED_CAROUSEL,
    PHOTO_POST,
    REEL,
    REEL_VIA_P,
    UNAVAILABLE_WITHOUT_LOGIN,
    codecs,
    download_instagram,
    require_cookies,
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
    def test_reel_and_p_link(self, tmp_path):
        cookies = require_cookies()
        for name, url in (("reel", REEL), ("p", REEL_VIA_P)):
            workdir = tmp_path / name
            workdir.mkdir()
            _check_reel(download_instagram(url, workdir, cookies), workdir)

    def test_photo_post_and_mixed_carousel(self, tmp_path):
        cookies = require_cookies()
        photo_dir, mixed_dir = tmp_path / "photo", tmp_path / "mixed"
        photo_dir.mkdir()
        mixed_dir.mkdir()
        _check_photo(download_instagram(PHOTO_POST, photo_dir, cookies), photo_dir)
        _check_mixed(download_instagram(MIXED_CAROUSEL, mixed_dir, cookies), mixed_dir)

    def test_source_cookies_file_is_not_modified(self, tmp_path):
        """Решение 7: yt-dlp получает копию; исходный файл остаётся байт в байт."""
        cookies = require_cookies()
        before = cookies.read_bytes()
        workdir = tmp_path / "w"
        workdir.mkdir()
        download_instagram(REEL, workdir, cookies)
        assert cookies.read_bytes() == before

    def test_broken_sessionid_is_login_required(self, tmp_path):
        """Аналог `--mode broken` из пробы 4: значение `sessionid` заменено — cookies отвергнуты."""
        source = require_cookies()
        broken = tmp_path / "broken.txt"
        shutil.copyfile(source, broken)
        lines = broken.read_text(encoding="utf-8").splitlines()
        patched, found = [], False
        for line in lines:
            fields = line.removeprefix("#HttpOnly_").split("\t")
            if len(fields) >= 7 and fields[5] == "sessionid":
                found = True
                fields[6] = "x" * 24
                line = ("#HttpOnly_" if line.startswith("#HttpOnly_") else "") + "\t".join(fields)
            patched.append(line)
        if not found:
            pytest.skip("в файле cookies нет sessionid")
        broken.write_text("\n".join(patched) + "\n", encoding="utf-8")
        workdir = tmp_path / "w"
        workdir.mkdir()
        with pytest.raises(LoginRequired):
            download_instagram(REEL, workdir, broken)
