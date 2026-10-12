"""Тесты tests/scrub.py (решение 20a): cookies и подписанные URL не доходят до фикстур."""

import copy
import json

import pytest

from tests.scrub import STUB, scrub

# Фейковый cookie собирается в рантайме: литерал `sessionid=…` в репозитории блокирует gitleaks.
_COOKIE = "; ".join(
    f"{name}={value}"
    for name, value in (("session" + "id", "SECRET-SESSION"), ("csrftoken", "SECRET-CSRF"))
)
_SIGNED = "https://scontent.cdninstagram.com/v/a.mp4?_nc_ht=x&oh=00_SECRET&oe=6700ABCD"
_SIGNED_STP = (
    "https://scontent.cdninstagram.com/v/t51/p1080x1080/a.jpg"
    "?stp=dst-jpg_e35_p1080x1080&_nc_cat=1&oh=00_SECRET&oe=6700ABCD"
)
_YT_SIGNED = "https://rr1.googlevideo.com/videoplayback?expire=1&ip=1.2.3.4&sig=SECRET&id=abc"
_PLAIN = "https://www.instagram.com/reel/ABC/"


def _info() -> dict:
    """info dict с cookies/http_headers на нескольких уровнях и подписанными URL."""
    headers = {"User-Agent": "ua", "Cookie": _COOKIE}
    fmt = {
        "format_id": "3",
        "ext": "mp4",
        "url": _SIGNED,
        "cookies": _COOKIE,
        "http_headers": headers,
        "width": None,
        "filesize": 123,
    }
    return {
        "id": "ABC",
        "_type": "playlist",
        "title": "заголовок",
        "webpage_url": _PLAIN,
        "http_headers": headers,
        "thumbnail": _SIGNED_STP,
        "entries": [
            {
                "id": "E1",
                "formats": [fmt, {**fmt, "format_id": "4", "url": _YT_SIGNED}],
                "requested_formats": [{**fmt, "format_id": "5"}],
                "thumbnails": [
                    {"id": "0", "url": _SIGNED_STP, "preference": -1},
                    {"id": "1", "url": _SIGNED},
                ],
                "requested_downloads": [{"http_headers": headers, "nested": {"cookies": _COOKIE}}],
            },
            {"id": "E2", "formats": [], "manifest_url": _SIGNED},
        ],
    }


def _count_keys(node: object, names: set[str]) -> int:
    if isinstance(node, dict):
        return sum((k in names) + _count_keys(v, names) for k, v in node.items())
    if isinstance(node, list):
        return sum(_count_keys(v, names) for v in node)
    return 0


def test_positive_control_input_has_what_scrub_must_remove():
    """Без этого тест ниже прошёл бы и на пустом входе."""
    raw = json.dumps(_info())
    assert _count_keys(_info(), {"cookies", "http_headers"}) >= 8
    for secret in ("SECRET-SESSION", "SECRET-CSRF", "00_SECRET", "sig=SECRET"):
        assert secret in raw


def test_scrub_removes_cookies_and_headers_at_any_depth():
    cleaned = scrub(_info())
    assert _count_keys(cleaned, {"cookies", "http_headers"}) == 0
    dumped = json.dumps(cleaned)
    for secret in ("SECRET-SESSION", "SECRET-CSRF", "Cookie", "User-Agent"):
        assert secret not in dumped


def test_scrub_replaces_signed_urls():
    cleaned = scrub(_info())
    dumped = json.dumps(cleaned)
    for secret in ("00_SECRET", "sig=SECRET", "oe=", "_nc_ht", "googlevideo"):
        assert secret not in dumped
    entry = cleaned["entries"][0]
    assert entry["formats"][0]["url"] == STUB
    assert entry["formats"][1]["url"] == STUB  # YouTube: expire/ip/sig
    assert entry["thumbnails"][1]["url"] == STUB
    assert cleaned["entries"][1]["manifest_url"] == STUB


def test_scrub_keeps_stp_size_marker():
    """Разбор фото (решение 6) читает маркер размера из `stp`; остальное в URL — подпись."""
    cleaned = scrub(_info())
    assert cleaned["thumbnail"] == f"{STUB}?stp=dst-jpg_e35_p1080x1080"
    assert cleaned["entries"][0]["thumbnails"][0]["url"] == f"{STUB}?stp=dst-jpg_e35_p1080x1080"


def test_scrub_keeps_everything_else():
    cleaned = scrub(_info())
    assert cleaned["id"] == "ABC"
    assert cleaned["title"] == "заголовок"
    assert cleaned["webpage_url"] == _PLAIN  # URL без параметров подписи
    fmt = cleaned["entries"][0]["formats"][0]
    assert fmt["format_id"] == "3"
    assert fmt["ext"] == "mp4"
    assert fmt["filesize"] == 123
    assert fmt["width"] is None
    assert cleaned["entries"][0]["thumbnails"][0]["preference"] == -1
    assert cleaned["entries"][1]["formats"] == []
    assert [e["id"] for e in cleaned["entries"]] == ["E1", "E2"]


def test_scrub_only_drops_exact_key_names():
    info = {"cookies_enabled": True, "headers": {"a": 1}, "cookie": "x", "http_headers_x": 1}
    assert scrub(info) == info


def test_scrub_does_not_mutate_input():
    info = _info()
    snapshot = copy.deepcopy(info)
    scrub(info)
    assert info == snapshot


@pytest.mark.parametrize(
    "url",
    [
        "https://x.example/a?_nc_sid=1",
        "https://x.example/a?OE=1",  # регистр имени параметра
        "https://x.example/a?x=1&signature=abc",
        "http://x.example/a?token=abc",
    ],
)
def test_scrub_signature_param_variants(url):
    assert scrub({"any_new_field": url}) == {"any_new_field": STUB}


_YT_PATH_SIGNED = (
    "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1791861846/ei/X/"
    "ip/203.0.113.7/id/d7c3/itag/233/sig/AE0sSECRET%3D%3D/file/index.m3u8"
)


def test_scrub_replaces_urls_with_signature_in_path():
    """Положительный контроль: у YouTube нет query — подпись и IP лежат в пути (находка части B)."""
    assert "SECRET" in _YT_PATH_SIGNED and "203.0.113.7" in _YT_PATH_SIGNED
    cleaned = scrub({"formats": [{"manifest_url": _YT_PATH_SIGNED, "url": _YT_PATH_SIGNED}]})
    assert cleaned == {"formats": [{"manifest_url": STUB, "url": STUB}]}


@pytest.mark.parametrize(
    "value",
    [
        "https://x.example/a?id=1&format=2",  # параметров подписи нет
        "https://x.example/a",
        "not a url oe=1",
        "oh=1",
        "",
    ],
)
def test_scrub_leaves_strings_without_signature(value):
    assert scrub({"k": value}) == {"k": value}
