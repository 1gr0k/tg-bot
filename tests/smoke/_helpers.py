"""Общее для дымовых тестов (реальная сеть, реальные yt-dlp и ffprobe). В CI не запускаются."""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tg_bot.downloader.instagram import InstagramDownloader
from tg_bot.downloader.port import DownloadResult
from tg_bot.downloader.shorts import ShortsDownloader
from tg_bot.downloader.ytdlp import ThreadRunner
from tg_bot.links import Link, find_links

# Ссылки из notes плана (раздел «Тестовые ссылки»).
REEL = "https://www.instagram.com/reel/DeRrI8_BTZz/"
REEL_VIA_P = "https://www.instagram.com/p/DeRrI8_BTZz/"
PHOTO_POST = "https://www.instagram.com/p/DdjcJv8Auv4/"
MIXED_CAROUSEL = "https://www.instagram.com/p/DeEjFYODJXW/"
UNAVAILABLE_WITHOUT_LOGIN = "https://www.instagram.com/p/BkfuX9UB-eK/"
SHORTS_LONG = "https://youtube.com/shorts/ZoAPORyKG4A"  # 135 с: 1080p — 58 440 000 байт (проба 5)
SHORTS_SHORT = "https://www.youtube.com/shorts/18NGQq7p3LY"
SHORTS_MISSING = "https://www.youtube.com/shorts/xxxxxxxxxxx"


def link(url: str) -> Link:
    (found,) = find_links([url])
    return found


def cookies_file() -> Path | None:
    """Путь к cookies — только из `IG_COOKIES_FILE`; значения не читаются и не печатаются."""
    value = os.environ.get("IG_COOKIES_FILE", "").strip()
    return Path(value) if value else None


def require_cookies() -> Path:
    path = cookies_file()
    if path is None or not path.is_file():
        pytest.skip("IG_COOKIES_FILE не задан или не файл: такой тест запускает владелец")
    return path


@contextmanager
def private_dir() -> Iterator[Path]:
    """Свой временный каталог для тестов с cookies: удаляется в `finally` при любом исходе.

    Копия cookies (`cookies.txt` загрузчика) и испорченная копия (`broken.txt`) лежат только
    внутри него: каталог `tmp_path` pytest переживает тест (политика хранения), и копия осталась
    бы на диске.
    """
    with tempfile.TemporaryDirectory(prefix="smoke-cookies-") as root:  # права 700
        yield Path(root)


def file_sha256(path: Path) -> str:
    """sha256 файла потоком: байты в памяти не остаются и в текст провала `assert` не попадут."""
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def write_broken_sessionid(source: Path, target: Path) -> bool:
    """Копия `source` с испорченным значением `sessionid` (аналог `--mode broken` пробы 4).

    Построчно, без хранения файла в памяти. Нет `sessionid` — `target` не остаётся, ответ `False`.
    """
    found = False
    with source.open(encoding="utf-8", errors="replace") as src, target.open("w") as out:
        for line in src:
            body = line.removeprefix("#HttpOnly_")
            fields = body.rstrip("\r\n").split("\t")
            if len(fields) == 7 and fields[5] == "sessionid":
                found = True
                fields[6] = "x" * 24
                line = line[: len(line) - len(body)] + "\t".join(fields) + "\n"
            out.write(line)
    if not found:
        target.unlink()
    return found


def download_instagram(url: str, workdir: Path, cookies: Path | None = None) -> DownloadResult:
    runner = ThreadRunner()
    try:
        return asyncio.run(InstagramDownloader(runner, cookies).download(link(url), workdir))
    finally:
        runner.close()


def download_shorts(url: str, workdir: Path) -> DownloadResult:
    runner = ThreadRunner()
    try:
        return asyncio.run(ShortsDownloader(runner).download(link(url), workdir))
    finally:
        runner.close()


def codecs(path: Path) -> dict[str, str]:
    """`{тип потока: кодек}` по ffprobe, например `{"video": "h264", "audio": "aac"}`."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    streams = json.loads(done.stdout)["streams"]
    return {s["codec_type"]: s["codec_name"] for s in streams}
