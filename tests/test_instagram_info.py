"""Разбор info dict Instagram на фикстурах из проб (чистые функции, решения 5–6)."""

import copy
import logging
from pathlib import Path

import pytest

from tests.fake_ytdlp import load_fixture
from tg_bot.downloader.instagram import (
    caption_of,
    classify,
    downloaded_path,
    entries_of,
    pick_photo_url,
)
from tg_bot.downloader.port import MediaKind

REEL = load_fixture("instagram_reel")
PHOTO = load_fixture("instagram_photo_post")
MIXED = load_fixture("instagram_mixed_carousel")
STUB = "<signed-url>"


def _thumbs(*stps: str | None) -> list[dict]:
    return [{"url": STUB if s is None else f"{STUB}?stp={s}"} for s in stps]


# --- entries_of / classify --------------------------------------------------------------------


def test_single_publication_is_one_entry():
    assert entries_of(REEL) == [REEL]
    assert entries_of(PHOTO) == [PHOTO]


def test_carousel_entries_keep_order():
    entries = entries_of(MIXED)
    assert [e["id"] for e in entries] == ["DeEi5N8sZfR", "DeEi5dYjHRK"]


def test_type_is_decided_by_response_not_by_link():
    """Reel, фото и видео внутри карусели различаются только по ответу yt-dlp."""
    assert classify(REEL) is MediaKind.VIDEO
    assert classify(PHOTO) is MediaKind.PHOTO
    assert [classify(e) for e in entries_of(MIXED)] == [MediaKind.PHOTO, MediaKind.VIDEO]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({}, None),
        ({"formats": [], "thumbnails": []}, None),
        ({"formats": None, "thumbnails": None}, None),
        ({"formats": "x", "thumbnails": "y"}, None),
        ({"formats": [{"url": "u"}]}, MediaKind.VIDEO),
        ({"formats": [{"url": "u"}], "thumbnails": _thumbs(None)}, MediaKind.VIDEO),
        ({"formats": None, "thumbnails": _thumbs(None)}, MediaKind.PHOTO),
        ({"thumbnails": _thumbs(None)}, MediaKind.PHOTO),
    ],
)
def test_classify_tolerates_missing_and_odd_values(entry, expected):
    assert classify(entry) is expected


@pytest.mark.parametrize("entries", [None, "x", 5, {"a": 1}])
def test_entries_not_a_list_means_single_publication(entries):
    info = {"id": "X", "entries": entries}
    assert entries_of(info) == [info]


def test_non_dict_entries_are_skipped_with_log(caplog):
    info = {"entries": [{"id": "A"}, None, "x", {"id": "B"}]}
    with caplog.at_level(logging.WARNING):
        assert [e["id"] for e in entries_of(info)] == ["A", "B"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.parametrize("info", [None, [], "x", 5])
def test_info_not_a_dict_gives_no_entries(info, caplog):
    with caplog.at_level(logging.WARNING):
        assert entries_of(info) == []
    assert caplog.records


def test_empty_entries_list_gives_no_entries():
    assert entries_of({"entries": []}) == []


# --- pick_photo_url ---------------------------------------------------------------------------


def test_photo_original_is_the_unmarked_variant():
    url = pick_photo_url(PHOTO["thumbnails"])
    assert url == f"{STUB}?stp=dst-jpg_e35_tt6"
    mixed_photo = entries_of(MIXED)[0]
    assert pick_photo_url(mixed_photo["thumbnails"]) == f"{STUB}?stp=dst-jpegr_e35_tt6"


def test_without_unmarked_largest_marker_wins():
    thumbs = PHOTO["thumbnails"][:-1]  # оригинал убран
    assert pick_photo_url(thumbs) == f"{STUB}?stp=dst-jpg_e35_p1080x1080_tt6"


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_largest_marker_by_area_not_by_position(order):
    items = _thumbs(
        "c0.1.2.3a_dst-jpg_s150x150_tt6", "dst-jpg_p1080x1080_tt6", "dst-jpg_s640x640_tt6"
    )
    assert pick_photo_url([items[i] for i in order]) == items[1]["url"]


def test_unmarked_beats_larger_marker_and_last_unmarked_wins():
    items = _thumbs("dst-jpg_p1080x1080_tt6", "dst-jpg_aaa", None, "dst-jpg_tt6")
    assert pick_photo_url(items) == items[3]["url"]


def test_url_without_stp_counts_as_unmarked():
    items = _thumbs("dst-jpg_s640x640_tt6", None)
    assert pick_photo_url(items) == STUB


def test_size_marker_is_read_only_from_stp_parameter():
    """Подобие маркера в пути или другом параметре — не размер: вариант остаётся «без маркера»."""
    in_path = "https://cdn.example/v/abc_s150x150_x.jpg?stp=dst-jpg_e35_tt6&other=p1080x1080"
    marked = f"{STUB}?stp=dst-jpg_p1080x1080_tt6"
    assert pick_photo_url([{"url": marked}, {"url": in_path}]) == in_path  # оригинал — in_path


@pytest.mark.parametrize(
    "thumbnails",
    [None, [], "x", 5, {"url": "u"}, [None, "x", 5, {}, {"url": None}, {"url": 5}, {"url": ""}]],
)
def test_pick_photo_url_tolerates_garbage(thumbnails):
    assert pick_photo_url(thumbnails) is None


def test_pick_photo_url_skips_bad_items_but_keeps_good_ones():
    items = [None, {"url": 5}, *_thumbs("dst-jpg_s640x640_tt6")]
    assert pick_photo_url(items) == f"{STUB}?stp=dst-jpg_s640x640_tt6"


def test_pick_photo_url_does_not_mutate_input():
    thumbs = copy.deepcopy(PHOTO["thumbnails"])
    pick_photo_url(thumbs)
    assert thumbs == PHOTO["thumbnails"]


# --- caption_of / downloaded_path -------------------------------------------------------------


def test_caption_from_top_level_and_from_entry():
    assert caption_of(MIXED).startswith("Ловлю солнечные лучики")
    assert caption_of(REEL) == "Угорели по искусству? #наполеоны #рилс #сибиряки"
    no_top = {"entries": [{"description": ""}, {"description": "из элемента"}]}
    assert caption_of(no_top) == "из элемента"


@pytest.mark.parametrize(
    "info",
    [
        None,
        {},
        {"description": None},
        {"description": "  \n"},
        {"description": 5},
        "x",
        {"entries": "x"},
    ],
)
def test_caption_missing_or_odd_is_empty_string(info):
    assert caption_of(info) == ""


def test_caption_keeps_text_as_is():
    assert caption_of({"description": "a\n\nb  "}) == "a\n\nb  "


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"requested_downloads": [{"filepath": "/w/a.mp4"}]}, Path("/w/a.mp4")),
        ({"requested_downloads": [{}, {"filepath": "/w/b.mp4"}]}, Path("/w/b.mp4")),
        ({"requested_downloads": []}, None),
        ({"requested_downloads": None}, None),
        ({"requested_downloads": [None, {"filepath": 5}, {"filepath": ""}]}, None),
        ({}, None),
        (None, None),
        ("x", None),
    ],
)
def test_downloaded_path(result, expected):
    assert downloaded_path(result) == expected
