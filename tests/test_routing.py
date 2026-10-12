"""Маршрутизация: платформа ссылки → загрузчик (тип публикации не учитывается)."""

import pytest

from tg_bot.downloader.port import DownloadError
from tg_bot.links import Link, Platform, PostKind, find_links
from tg_bot.routing import DownloaderRegistry, UnsupportedLink


class Marker:
    """Различимый загрузчик: сверяем по идентичности объекта."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def download(self, link, workdir):  # pragma: no cover — маршрутизация не вызывает
        raise AssertionError("не должен вызываться")


INSTAGRAM, SHORTS = Marker("instagram"), Marker("shorts")


@pytest.fixture
def registry():
    return DownloaderRegistry({Platform.INSTAGRAM: INSTAGRAM, Platform.YOUTUBE: SHORTS})


def _link(url: str) -> Link:
    (link,) = find_links([url])
    return link


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/reel/DeRrI8_BTZz/", INSTAGRAM),
        ("https://www.instagram.com/p/DdjcJv8Auv4/", INSTAGRAM),
        ("https://www.instagram.com/someone/reel/DeRrI8_BTZz/", INSTAGRAM),
        ("https://youtube.com/shorts/ZoAPORyKG4A", SHORTS),
        ("https://m.youtube.com/shorts/BGQWPY4IigY", SHORTS),
    ],
)
def test_each_platform_goes_to_its_downloader(registry, url, expected):
    assert registry.for_link(_link(url)) is expected


def test_post_kind_does_not_affect_routing(registry):
    """`/p/` и `/reel/` — один загрузчик: тип публикации определяет он сам (решение 5)."""
    reel = _link("https://www.instagram.com/reel/DeRrI8_BTZz/")
    post = _link("https://www.instagram.com/p/DeRrI8_BTZz/")
    assert (reel.kind, post.kind) == (PostKind.REEL, PostKind.POST)
    assert registry.for_link(reel) is registry.for_link(post) is INSTAGRAM


def test_platform_without_downloader_is_unsupported_not_keyerror():
    registry = DownloaderRegistry({Platform.INSTAGRAM: INSTAGRAM})
    link = _link("https://youtube.com/shorts/ZoAPORyKG4A")
    with pytest.raises(UnsupportedLink) as caught:
        registry.for_link(link)
    assert not isinstance(caught.value, KeyError)
    assert isinstance(caught.value, DownloadError)  # pipeline ловит одним except
    assert "youtube" in str(caught.value)
    assert caught.value.user_message.strip()
    # положительный контроль: зарегистрированная платформа в том же реестре работает
    assert registry.for_link(_link("https://www.instagram.com/reel/DeRrI8_BTZz/")) is INSTAGRAM


def test_empty_registry_supports_nothing():
    with pytest.raises(UnsupportedLink):
        DownloaderRegistry({}).for_link(_link("https://www.instagram.com/reel/DeRrI8_BTZz/"))


def test_registry_copies_mapping():
    source = {Platform.INSTAGRAM: INSTAGRAM}
    registry = DownloaderRegistry(source)
    source[Platform.YOUTUBE] = SHORTS  # правка исходного словаря после создания
    with pytest.raises(UnsupportedLink):
        registry.for_link(_link("https://youtube.com/shorts/ZoAPORyKG4A"))
