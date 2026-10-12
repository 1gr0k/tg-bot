"""Хелперы дымовых тестов с cookies (`tests/smoke/_helpers.py`): каталог с копией cookies не
переживает тест, сравнение исходного файла — по sha256, испорченный `sessionid` — построчно.

Файлы cookies здесь фейковые, значения собраны в рантайме: настоящих cookies в тестах нет.
"""

import stat
import uuid
from pathlib import Path

import pytest

from tests.smoke._helpers import file_sha256, private_dir, write_broken_sessionid
from tg_bot.downloader.ytdlp import copy_cookies


def _line(name: str, value: str, *, http_only: bool = False) -> str:
    domain = ("#HttpOnly_" if http_only else "") + ".instagram.com"
    return "\t".join([domain, "TRUE", "/", "TRUE", "1999999999", name, value])


@pytest.fixture
def fake_cookies(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    values = {name: f"{name}-{uuid.uuid4().hex}" for name in ("csrftoken", "sessionid", "mid")}
    path = tmp_path / "src" / "instagram.txt"
    path.parent.mkdir()
    lines = [_line(n, v, http_only=(n == "sessionid")) for n, v in values.items()]
    path.write_text("# Netscape HTTP Cookie File\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path, values


def test_private_dir_with_cookies_copy_is_gone_after_use(fake_cookies):
    source, _ = fake_cookies
    with private_dir() as root:
        workdir = root / "w"
        workdir.mkdir()
        copy_cookies(source, workdir)  # то же, что делает загрузчик
        assert (workdir / "cookies.txt").is_file()  # положительный контроль: копия была
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not root.exists()
    assert source.is_file()  # исходник не тронут


def test_private_dir_is_removed_when_the_test_fails(fake_cookies):
    source, _ = fake_cookies
    with pytest.raises(AssertionError), private_dir() as root:
        workdir = root / "w"
        workdir.mkdir()
        copy_cookies(source, workdir)
        (root / "broken.txt").write_text("x")
        raise AssertionError("тест упал")
    assert not root.exists()


def test_each_use_gets_its_own_directory():
    with private_dir() as first, private_dir() as second:
        assert first != second


def test_tmp_path_is_not_retained_after_tests(request):
    """`tmp_path` pytest иначе переживает тест (по умолчанию — три последних прогона)."""
    assert request.config.getini("tmp_path_retention_policy") == "none"


def test_sha256_changes_with_content_and_never_exposes_bytes(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"secret-a")
    before = file_sha256(path)
    assert before == file_sha256(path)
    path.write_bytes(b"secret-b")
    assert file_sha256(path) != before
    assert b"secret" not in before.encode()
    assert len(before) == 64


def test_broken_sessionid_replaces_only_that_value(fake_cookies, tmp_path):
    source, values = fake_cookies
    before = file_sha256(source)
    target = tmp_path / "broken.txt"
    assert write_broken_sessionid(source, target) is True
    text = target.read_text(encoding="utf-8")
    assert values["sessionid"] not in text
    assert values["csrftoken"] in text and values["mid"] in text
    assert "#HttpOnly_.instagram.com" in text  # префикс HttpOnly сохранён
    assert text.count("\n") == source.read_text(encoding="utf-8").count("\n")
    assert file_sha256(source) == before  # исходный файл не менялся


def test_broken_sessionid_without_session_leaves_no_file(tmp_path):
    source = tmp_path / "c.txt"
    source.write_text(_line("csrftoken", "v") + "\n", encoding="utf-8")
    target = tmp_path / "broken.txt"
    assert write_broken_sessionid(source, target) is False
    assert not target.exists()
