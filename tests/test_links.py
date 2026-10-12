import pytest

from tg_bot.links import MAX_LINKS_PER_MESSAGE, Link, Platform, PostKind, find_links

IG = Platform.INSTAGRAM
YT = Platform.YOUTUBE


def reel(code: str) -> Link:
    return Link(IG, PostKind.REEL, code, f"https://www.instagram.com/reel/{code}/")


def post(code: str) -> Link:
    return Link(IG, PostKind.POST, code, f"https://www.instagram.com/p/{code}/")


def short(video_id: str) -> Link:
    return Link(YT, PostKind.SHORTS, video_id, f"https://www.youtube.com/shorts/{video_id}")


CODE = "DeRrI8_BTZz"
VID = "dQw4w9WgXcQ"

POSITIVE = [
    # Instagram: домены и тип пути (ТЗ 2.1, 2.2)
    ("https://www.instagram.com/reel/" + CODE + "/", [reel(CODE)]),
    ("https://instagram.com/reel/" + CODE + "/", [reel(CODE)]),
    ("https://m.instagram.com/reel/" + CODE + "/", [reel(CODE)]),
    ("https://www.instagram.com/reels/" + CODE + "/", [reel(CODE)]),
    ("https://www.instagram.com/p/" + CODE + "/", [post(CODE)]),
    ("http://instagram.com/p/" + CODE, [post(CODE)]),
    ("instagram.com/reel/" + CODE, [reel(CODE)]),
    # вариант с именем пользователя
    ("https://www.instagram.com/some.user_1/reel/" + CODE + "/", [reel(CODE)]),
    ("https://www.instagram.com/some.user_1/p/" + CODE + "/", [post(CODE)]),
    # параметры, фрагмент, косая черта, регистр схемы и хоста
    ("https://www.instagram.com/reel/" + CODE + "/?igsh=abc123&utm_source=x", [reel(CODE)]),
    ("https://www.instagram.com/reel/" + CODE + "?igsh=abc", [reel(CODE)]),
    ("https://www.instagram.com/reel/" + CODE + "/#frag", [reel(CODE)]),
    ("HTTPS://WWW.Instagram.com/reel/" + CODE + "/", [reel(CODE)]),
    # идентификатор с дефисом и подчёркиванием не обрезается
    ("https://www.instagram.com/p/a-b_C-d_E12/", [post("a-b_C-d_E12")]),
    # регистр пути: IGNORECASE
    ("https://www.instagram.com/P/" + CODE + "/", [post(CODE)]),
    ("https://www.instagram.com/Reel/" + CODE + "/", [reel(CODE)]),
    ("https://www.instagram.com/REELS/" + CODE + "/", [reel(CODE)]),
    # конец кода — по ASCII-классу: кириллица сразу за кодом не часть кода
    ("https://www.instagram.com/reel/ABC123посмотри", [reel("ABC123")]),  # noqa: RUF001 — кириллица нужна намеренно
    ("https://www.youtube.com/shorts/" + VID + "смотри", [short(VID)]),
    # положительный контроль к предыдущему: `_` и `-` — часть кода
    ("https://www.instagram.com/reel/ABC123_x", [reel("ABC123_x")]),
    ("https://www.instagram.com/reel/ABC123-x", [reel("ABC123-x")]),
    # YouTube Shorts (ТЗ 2.3)
    ("https://www.youtube.com/shorts/" + VID, [short(VID)]),
    ("https://youtube.com/shorts/" + VID, [short(VID)]),
    ("https://m.youtube.com/shorts/" + VID, [short(VID)]),
    ("https://www.youtube.com/shorts/" + VID + "/", [short(VID)]),
    ("https://www.youtube.com/shorts/" + VID + "?feature=share", [short(VID)]),
    ("https://www.youtube.com/shorts/-_-_-_-_-_-", [short("-_-_-_-_-_-")]),
    # ссылка внутри текста, знаки препинания вокруг (ТЗ 3.1)
    ("Смотри " + "https://www.instagram.com/reel/" + CODE + "/ вот это!", [reel(CODE)]),
    ("(https://www.instagram.com/reel/" + CODE + "/)", [reel(CODE)]),
    ("ссылка: https://www.instagram.com/reel/" + CODE + ".", [reel(CODE)]),
    ("https://www.instagram.com/reel/" + CODE + "/\nи ещё текст", [reel(CODE)]),
    # несколько ссылок — порядок появления, смешанные платформы
    (
        "https://www.youtube.com/shorts/" + VID + " и https://instagram.com/p/" + CODE,
        [short(VID), post(CODE)],
    ),
    # дубликаты схлопываются; /reel/<код> и /p/<код> — одна публикация
    (
        "https://www.instagram.com/reel/"
        + CODE
        + "/ https://instagram.com/reel/"
        + CODE
        + "?igsh=1",
        [reel(CODE)],
    ),
    (
        "https://www.instagram.com/p/" + CODE + "/ https://www.instagram.com/reel/" + CODE + "/",
        [post(CODE)],
    ),
]


@pytest.mark.parametrize(("text", "expected"), POSITIVE)
def test_positive(text: str, expected: list[Link]) -> None:
    assert find_links([text]) == expected


NEGATIVE = [
    # неподдерживаемое (ТЗ 2.4)
    "https://youtu.be/" + VID,
    "https://youtu.be/" + VID + "?si=abc",
    "https://www.youtube.com/watch?v=" + VID,
    "https://www.youtube.com/playlist?list=PLabc",
    "https://www.instagram.com/stories/some.user/1234567890123456789/",
    "https://www.instagram.com/stories/p/1234567890/",
    "https://www.instagram.com/some.user/",
    "https://www.instagram.com/explore/tags/cats/",
    "https://www.instagram.com/reels/",
    "https://www.instagram.com/reels/audio/1234567890/",
    "https://www.tiktok.com/@user/video/123",
    "https://vk.com/video-1_2",
    # Shorts: идентификатор неверной длины
    "https://www.youtube.com/shorts/short",
    "https://www.youtube.com/shorts/" + VID + "x",
    # чужие домены, похожие на поддерживаемые
    "https://evilinstagram.com/reel/" + CODE + "/",
    "https://instagram.com.evil.example/reel/" + CODE + "/",
    "https://notyoutube.com/shorts/" + VID,
    "https://example.com/?u=instagram.com/reel/" + CODE,
    "https://example.com/instagram.com/reel/" + CODE,
    # перед доменом `.`, `@`, `-`: поддомен, адрес с логином, часть слова
    "https://x.instagram.com/reel/" + CODE,
    "https://user@instagram.com/reel/" + CODE,
    "https://my-instagram.com/reel/" + CODE,
    "https://x.youtube.com/shorts/" + VID,
    "https://user@youtube.com/shorts/" + VID,
    "https://my-youtube.com/shorts/" + VID,
    # не-ссылки
    "",
    "   ",
    "просто текст про reel и shorts",
    "instagram reel " + CODE,
    "instagram.com",
    "/reel/" + CODE,
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_negative(text: str) -> None:
    assert find_links([text]) == []


def test_negatives_are_negative_only_because_of_the_rules() -> None:
    """Положительный контроль: те же формы с поддерживаемым путём находятся."""
    assert find_links(["https://www.instagram.com/reel/" + CODE + "/"]) == [reel(CODE)]
    assert find_links(["https://www.youtube.com/shorts/" + VID]) == [short(VID)]
    assert find_links(["https://www.instagram.com/some.user/reel/" + CODE + "/"]) == [reel(CODE)]
    assert find_links(["https://www.youtube.com/shorts/" + VID + "?x=1"]) == [short(VID)]


def test_p_does_not_determine_publication_type() -> None:
    """`/p/<код>` — Instagram без предположения о типе: PostKind.POST, не «фото» и не «видео»."""
    (link,) = find_links(["https://www.instagram.com/p/" + CODE + "/"])
    assert link.platform is IG
    assert link.kind is PostKind.POST
    assert link.url == "https://www.instagram.com/p/" + CODE + "/"
    # PostKind — только отметка для лога: значения «фото/карусель/видео» в нём нет.
    assert {k.value for k in PostKind} == {"reel", "post", "shorts"}


def test_reel_and_p_with_same_code_differ_in_kind_only() -> None:
    (a,) = find_links(["https://www.instagram.com/reel/" + CODE])
    (b,) = find_links(["https://www.instagram.com/p/" + CODE])
    assert (a.platform, a.media_id) == (b.platform, b.media_id)
    assert a.kind is PostKind.REEL
    assert b.kind is PostKind.POST


def test_candidates_are_combined_and_deduplicated() -> None:
    """text, caption и url из text_link — отдельные кандидаты одного сообщения."""
    other = "AbCdEfGhIjK"
    candidates = [
        "смотри",
        "https://www.instagram.com/reel/" + CODE + "/",
        "https://www.instagram.com/reel/" + CODE + "/?igsh=1",
        "https://www.instagram.com/p/" + other + "/",
    ]
    assert find_links(candidates) == [reel(CODE), post(other)]


def test_no_candidates() -> None:
    assert find_links([]) == []


def test_limit_per_message() -> None:
    codes = [f"code{i:02d}xxxxx" for i in range(MAX_LINKS_PER_MESSAGE + 3)]
    text = " ".join("https://www.instagram.com/reel/" + c + "/" for c in codes)
    result = find_links([text])
    assert [link.media_id for link in result] == codes[:MAX_LINKS_PER_MESSAGE]
    # положительный контроль: ровно на пределе — все на месте
    assert len(find_links([" ".join("instagram.com/p/" + c for c in codes[:5])])) == 5


def test_duplicates_do_not_use_up_the_limit() -> None:
    codes = [f"code{i:02d}xxxxx" for i in range(MAX_LINKS_PER_MESSAGE)]
    text = " ".join("https://instagram.com/p/" + c for c in codes for _ in range(3))
    assert [link.media_id for link in find_links([text])] == codes
