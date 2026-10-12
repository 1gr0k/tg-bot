"""Разбор ссылок: поиск и нормализация ссылок на Instagram и YouTube Shorts в тексте.

Чистые функции над строками, без сети. Кандидаты — `text`/`caption` сообщения и `url` из
entities `text_link` (извлекает обработчик).

`/p/<код>` не определяет тип публикации: это может быть фото, карусель или одиночное видео.
`PostKind.POST` — только отметка для лога; что внутри, решает загрузчик по ответу yt-dlp.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

MAX_LINKS_PER_MESSAGE = 5


class Platform(StrEnum):
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"


class PostKind(StrEnum):
    REEL = "reel"
    POST = "post"
    SHORTS = "shorts"


@dataclass(frozen=True, slots=True)
class Link:
    platform: Platform
    kind: PostKind
    media_id: str  # shortcode / id видео
    url: str  # нормализованная каноническая ссылка


# Перед ссылкой — не часть слова или адреса: иначе заденем `evilinstagram.com`,
# `x.com/?u=instagram.com/…`.
_START = r"(?<![\w.@=/-])"
_SCHEME = r"(?:https?://)?"
_WWW = r"(?:www\.|m\.)?"
# После идентификатора — не символ идентификатора (параметры, фрагмент, `/`, пробел, конец).
# ASCII-класс, а не `\w`: в Python `\w` включает кириллицу, и `…/reel/ABC123посмотри` пропало бы.
_END = r"(?![A-Za-z0-9_-])"

_INSTAGRAM = re.compile(
    _START + _SCHEME + _WWW + r"instagram\.com"
    # Имя пользователя необязательно: `/<user>/reel/<код>`; `stories` — раздел, не имя.
    r"/(?:(?!stories/)[A-Za-z0-9_.]+/)?"
    r"(?P<kind>reels?|p)/"
    # `/reels/audio/<id>` — страница звука, не публикация.
    r"(?!audio/)(?P<code>[A-Za-z0-9_-]+)" + _END,
    re.IGNORECASE,
)
_SHORTS = re.compile(
    _START + _SCHEME + _WWW + r"youtube\.com/shorts/(?P<id>[A-Za-z0-9_-]{11})" + _END,
    re.IGNORECASE,
)


def _find_in(text: str) -> list[tuple[int, Link]]:
    found: list[tuple[int, Link]] = []
    for m in _INSTAGRAM.finditer(text):
        code = m["code"]
        if m["kind"].lower() == "p":
            kind, path = PostKind.POST, "p"
        else:
            kind, path = PostKind.REEL, "reel"
        url = f"https://www.instagram.com/{path}/{code}/"
        found.append((m.start(), Link(Platform.INSTAGRAM, kind, code, url)))
    for m in _SHORTS.finditer(text):
        video_id = m["id"]
        url = f"https://www.youtube.com/shorts/{video_id}"
        found.append((m.start(), Link(Platform.YOUTUBE, PostKind.SHORTS, video_id, url)))
    found.sort(key=lambda item: item[0])
    return found


def find_links(candidates: Iterable[str]) -> list[Link]:
    """Ссылки из кандидатов — в порядке появления, без дубликатов, не больше пяти.

    Дубликат — та же публикация (платформа и идентификатор): `/reel/<код>` и `/p/<код>` — одна.
    """
    result: list[Link] = []
    seen: set[tuple[Platform, str]] = set()
    for text in candidates:
        for _, link in _find_in(text):
            key = (link.platform, link.media_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(link)
            if len(result) == MAX_LINKS_PER_MESSAGE:
                return result
    return result
