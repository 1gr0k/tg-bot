"""Очистка info dict yt-dlp перед сохранением в фикстуры (решение 20a).

yt-dlp кладёт cookies в поле `cookies` формата и в `http_headers` (заголовок `Cookie`);
URL CDN подписаны (`oh=`, `oe=`, `_nc_*`, `sig`, `expire`…). `scrub` рекурсивно удаляет
первые и заменяет вторые заглушкой. Это одна из двух мер вместе с правилом gitleaks на
`sessionid`: `scrub` работает по известным ключам и параметрам, gitleaks — по шаблону при коммите.

Подписанным считается **любой** строковый http(s)-URL с параметром подписи, а не только значения
известных ключей (`url`, `manifest_url`, `thumbnails[].url`): новое поле yt-dlp с таким URL тоже
будет заменено.

Параметр `stp` (маркер размера картинки Instagram, `…_p1080x1080_…`) не секрет и нужен разбору
фото (решение 6), поэтому заглушка его сохраняет: `<signed-url>?stp=…`.
"""

from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

STUB = "<signed-url>"
DROPPED_KEYS = frozenset({"cookies", "http_headers"})

_SIGNATURE_PARAMS = frozenset(
    {
        "oh", "oe", "sig", "lsig", "signature", "expire", "expires", "token", "pot",
        "ip", "ipbits", "sparams", "lsparams", "key", "ei", "hash", "hmac", "policy",
        "key-pair-id", "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    }
)  # fmt: skip
_SIGNATURE_PREFIXES = ("_nc_",)


def _is_signature_param(name: str) -> bool:
    name = name.lower()
    return name in _SIGNATURE_PARAMS or name.startswith(_SIGNATURE_PREFIXES)


def _scrub_url(value: str) -> str:
    """Подписанный URL → заглушка (с `stp`, если он был); остальные строки — как есть."""
    if not value.startswith(("http://", "https://")):
        return value
    query = parse_qsl(urlsplit(value).query, keep_blank_values=True)
    if not any(_is_signature_param(name) for name, _ in query):
        return value
    for name, marker in query:
        if name == "stp":
            return f"{STUB}?stp={quote(marker, safe='')}"
    return STUB


def scrub(info: Any) -> Any:
    """Копия `info` без `cookies`/`http_headers` на любой глубине и без подписанных URL."""
    if isinstance(info, dict):
        return {key: scrub(value) for key, value in info.items() if key not in DROPPED_KEYS}
    if isinstance(info, list | tuple):
        return [scrub(item) for item in info]
    if isinstance(info, str):
        return _scrub_url(info)
    return info
