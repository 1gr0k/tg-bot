# Этап 1 — сквозной конвейер

План двухуровневый: подробности проб, таблицы зубов и итоги частей — в
[`stage-1-pipeline-notes.md`](stage-1-pipeline-notes.md). Читать по разделам: `grep -n '^#'`.
Числа лимитов в этом плане — **ожидания для проверки пробами**, не значения для кода.

## 1. Контекст

Кода бота нет; есть платформа этапа 0 (`scripts/check.sh`, хуки, агенты, скиллы). Полный
`check.sh` красный: тестов нет (pytest 5). Репозиторий на GitHub (`1gr0k/tg-bot`), CI нет.

Цель этапа:
- сквозной конвейер в личном чате с владельцем: ссылка → разбор → маршрутизация → загрузчик →
  отправка; YouTube Shorts, Instagram Reels, Instagram Posts (фото, видео, карусель);
- доступ: только личный чат с `OWNER_ID`; всё остальное — пропуск с записью в лог;
- защита секретов в git (gitleaks pre-commit + CI) — до первого кода с токеном;
- CI на GitHub Actions: lint, hooks, test, gitleaks.

Бот — существующий, старый токен; состоит в группах прошлой версии. Из групп не выходим (этап 2).

## 2. Решения

Пометка **[проба N]** — решение пересматривается по результату пробы.

1. **Доступ этапа 1 — фильтр в outer-middleware Dispatcher**: апдейт проходит, только если это
   `message` из `chat.type == private` и `from_user.id == OWNER_ID`; иначе — `INFO` в лог (тип
   апдейта, тип и id чата) и выход. Причина: проверка до любой работы в одном месте, этап 2
   заменит функцию решения, а не обработчики. Отвергнуто: фильтр на каждом обработчике (легко
   забыть на новом); выход из групп сейчас (ТЗ 4.4 целиком — этап 2).
2. **Накопленные апдейты при старте** — [проба 0]. Предварительно: `drop_pending_updates=True`
   (ссылки многочасовой давности и апдейты из старых групп не нужны). Отвергнуто: обработать всё
   накопленное (поток ответов на старые ссылки).
3. **Временный каталог принадлежит вызывающему, а не результату**: конвейер создаёт каталог
   (`tempfile.TemporaryDirectory` в `try/finally` вокруг загрузки и отправки), загрузчик пишет
   только в переданный `workdir`. Причина: одно место очистки при любом исходе, включая отмену.
   Отвергнуто: `tmpdir` в `DownloadResult` (ТЗ 13) — утечка при исключении между созданием
   каталога и возвратом результата, владение размазано.
4. **Порт загрузчика — `Protocol` + frozen-dataclass результата + своя иерархия ошибок** без типов
   yt-dlp и aiogram (черновик — раздел 5). Отвергнуто: возвращать info dict yt-dlp (протечка).
5. **Один адаптер yt-dlp на платформу** (`InstagramDownloader`, `ShortsDownloader`) с общим
   модулем: опции, перевод ошибок, запуск в потоке. Reel и Post — один загрузчик Instagram,
   тип публикации — информация для лога. Отвергнуто: отдельный загрузчик Reels/Posts (ТЗ 13) —
   yt-dlp обслуживает оба одним экстрактором [проба 4].
6. **Фото Instagram** — [проба 4]. Если yt-dlp фото не скачивает: альтернативы для решения после
   пробы — (а) URL изображения из info dict (`thumbnails`/`url`) + загрузка через aiohttp
   (уже зависимость aiogram); (б) gallery-dl как библиотека (новая зависимость). Не выбираем до пробы.
7. **Cookies Instagram** — [проба 4], выбор после неё. Если yt-dlp пишет cookiejar обратно в
   `cookiefile`, параллельные загрузки не должны переписывать `data/instagram.txt` вразнобой.
   Варианты:
   - (а) **копия** файла во временном каталоге загрузки (права 600), оригинал не трогается.
     Цена: обновления сессии от Instagram теряются — cookies истекают быстрее, чаще ручная замена;
   - (б) **атомарная запись обновлённых cookies обратно**: загрузка работает с копией, после неё
     изменённые cookies пишутся во временный файл рядом с оригиналом (права 600) и
     `os.replace` под общим `asyncio.Lock`. Цена: сложнее (слияние при параллельных загрузках —
     последняя запись выигрывает, тест на прерванную запись), зато сессия живёт дольше.
   Если проба покажет, что yt-dlp переписывает файл **с теми же значениями** — вариант (а) без
   потерь; если **меняет значения** — выбор между (а) и (б) по цене.
8. **Тайм-аут загрузки**: `asyncio.timeout` вокруг `asyncio.to_thread`; при срабатывании —
   флаг `threading.Event`, который проверяет progress-hook yt-dlp (исключение прерывает загрузку),
   и ожидание завершения потока до удаления каталога. Плюс `socket_timeout`. Значение — [проба 6].
   Отвергнуто: только `asyncio.timeout` — поток продолжает писать в удалённый каталог.
9. **Ресурсы (ТЗ 9.4)**: `asyncio.Semaphore` на одновременные загрузки (2); защита от повтора —
   множество «в работе» по нормализованной ссылке (повтор той же ссылки, пока идёт загрузка, —
   пропуск с логом). Лимит размера — `max_filesize` yt-dlp + фильтр формата [проба 1, 5].
10. **Отправка — две части**: чистая функция `plan_sends(result, limits) -> list[Send]`
    (что и в каком порядке отправить) и тонкий исполнитель поверх aiogram `Bot`. Причина:
    разбиение и подписи тестируются таблицей без фейков. Отвергнуто: собственный порт Telegram
    поверх aiogram — фейк сессии aiogram уже даёт тест без сети (решение 13).
11. **Медиагруппы**: элементов больше лимита [проба 2] — разбиение на группы почти равного
    размера (11 → 6+5, а не 10+1: группа из одного элемента невозможна); один элемент →
    `sendVideo`/`sendPhoto`; порядок сохраняется.
12. **Подпись**: plain text (`parse_mode=None` явно — в описаниях `<`, `_`, `*`). Влезает в лимит
    [проба 3] — подпись к первому элементу; не влезает — медиа без подписи + полный текст
    следующим сообщением(ями) с разбиением по лимиту текста [проба 3] по границе пробела.
    Отвергнуто: обрезка с «…» — ТЗ 5.4 предпочитает полноту.
13. **Фейк Telegram — подмена сессии aiogram** (`BaseSession`, записывает запросы, отвечает
    заданным), токен в тестах — `42:TEST` (не совпадает с правилом gitleaks). Причина: обработчики
    тестируются через `Dispatcher.feed_update` вместе с настоящей сборкой запросов aiogram.
14. **Разбор ссылок** — чистые функции над строками; кандидаты — `text`/`caption` и `url` из
    entities `text_link` (извлекает обработчик). Поддержка: `instagram.com` с `www.`/`m.`,
    `/reel/`, `/reels/`, `/p/`, вариант с именем пользователя `/<user>/(reel|p)/<code>`;
    `youtube.com/shorts/<id>` с `www.`/`m.`; параметры, фрагмент и `/` в конце отбрасываются;
    дубликаты в одном сообщении схлопываются; не больше 5 ссылок на сообщение.
    `youtu.be/<id>` — не поддерживается (это ссылка на обычное видео, ТЗ 2.4).
15. **Структура пакета — плоская `tg_bot/` в корне**, `package = false`, pytest
    `pythonpath = ["."]`, запуск `uv run python -m tg_bot`. Отвергнуто: src-layout с
    build-backend — приложение не распространяется как пакет, лишний шаг сборки.
16. **Конфигурация** — dataclass `Settings` (`bot_token` с `repr=False`), `load_settings(environ)`
    с `ConfigError`, где в сообщении имя переменной, но не значение. `BOT_TOKEN`, `OWNER_ID`
    обязательны; `IG_COOKIES_FILE` необязателен (нет — Instagram без cookies; задан — файл
    существует и читается, права шире 600 — предупреждение). Отвергнуто: pydantic-settings
    (зависимость ради трёх переменных).
17. **Зависимости**: `uv add 'aiogram>=<версия проб>,<4' 'yt-dlp>=<версия проб>'` (экстра
    `yt-dlp[default]` — если проба 5 покажет, что для YouTube нужен `yt-dlp-ejs`). Точные версии —
    `uv.lock`; нижняя граница = версия, на которой шли пробы (не работаем на непроверенной более
    старой). yt-dlp без верхней границы: обновление только через `uv lock --upgrade-package
    yt-dlp` (ТЗ 10). В CI — `uv lock --check` (lock соответствует `pyproject.toml`).
18. **gitleaks pre-commit**: `scripts/git-hooks/pre-commit` (`#!/bin/bash`, `set -euo pipefail`):
    нет `gitleaks` в PATH — сообщение и exit 1 (fail-closed); иначе
    `gitleaks git --pre-commit --staged --redact --no-banner`. Включение:
    `git config core.hooksPath scripts/git-hooks` (строка в `CLAUDE.md` → Commands).
    `.gitleaks.toml`: `[extend] useDefault = true` + свои правила (встроенные требуют ключевое
    слово «telegr» рядом и строку `BOT_TOKEN=…` могут не поймать; cookies Instagram, скорее
    всего, не ловят вовсе):
    - **токен Telegram** — широкий шаблон под оба формата: современный (ID 8–10 цифр, вторая
      часть `AA…`, 35 символов) и старый (ID короче, вторая часть не с `AA`):
      `\b\d{5,12}:[0-9A-Za-z_-]{30,40}\b`;
    - **cookies Instagram, Netscape**: строка домена `instagram.com` с именем `sessionid` —
      `instagram\.com\t(TRUE|FALSE)\t\S*\t(TRUE|FALSE)\t\d+\tsessionid\t\S+`;
    - **cookies Instagram, заголовок/строка cookies**: `\bsessionid=[^;\s"']{8,}`.

    Положительный контроль — **на настоящих секретах** (шаг пользователя, часть A):
    `gitleaks dir --redact -v .env` и `gitleaks dir --redact -v data/instagram.txt` (с
    `--config .gitleaks.toml`) — каждый должен дать ≥1 находку. Не нашлось — пользователь приносит
    редактированный вывод, дописывается правило, повторяем. Результат — в notes (без значений).

    Автотест хука — `scripts/test-git-hooks.sh` в секции `hooks` `check.sh`: временный репозиторий
    в `$TMPDIR`, фейковые секреты **собираются в рантайме** (литералов в репозитории нет):
    токен в **обоих форматах** (современный: 10 цифр + `AA` + 33 символа; старый: короткий ID +
    35 символов не с `AA`), строка Netscape с `sessionid`, строка `sessionid=…`. Каждый staged-файл
    с секретом → коммит отклонён; тот же файл без секрета → проходит; PATH без gitleaks → отклонён.
    Отвергнуто: фреймворк pre-commit (Python-зависимость ради одного хука).
19. **CI** (`.github/workflows/ci.yml`, `ubuntu-latest`, на PR и push в master): job’ы `lint`
    (`uv lock --check`, `check.sh --only lint`), `hooks` (`check.sh --only hooks`, нужен gitleaks),
    `test` (`check.sh --only test`), `gitleaks` (`gitleaks git` по всей истории, `--redact`).
    uv — `astral-sh/setup-uv` с фиксированной версией; gitleaks — релизный бинарник фиксированной
    версии с проверкой sha256. Отвергнуто: `gitleaks-action` (обёртка, лицензия для org-аккаунтов).
20. **Дымовые тесты загрузчиков** — `tests/smoke/`, маркер `smoke`, исключён по умолчанию
    (`addopts = "-m 'not smoke'"`); запуск вручную `uv run pytest -m smoke`; Instagram с cookies —
    запускает пользователь. В CI не идут (ТЗ 11).

    20a. **Очистка вывода yt-dlp от cookies — механически**: yt-dlp кладёт cookies в поле
    `cookies` формата и в `http_headers` (заголовок `Cookie`). Функция `scrub(info)` рекурсивно
    удаляет ключи `cookies` и `http_headers` на любой глубине (форматы, `entries`,
    `requested_formats`), подписанные URL CDN (`url`, `manifest_url`, `thumbnails[].url` с
    параметрами подписи — `oh=`, `oe=`, `_nc_*`, `sig`, `expire` и т.п.) заменяет заглушкой
    `<signed-url>`.
    - В скриптах проб — **до** любой печати и сохранения JSON.
    - В части B — `tests/scrub.py` (утилита для подготовки фикстур) с тестом: info dict с
      `cookies`/`http_headers` на нескольких уровнях и подписанным URL → в результате их нет
      (положительный контроль: без `scrub` тест находит их).
    - Фикстуры в `tests/` — только из очищенного вывода. Правило gitleaks на `sessionid`
      (решение 18) — второй слой.
21. **Лимит отправки 50 МБ** [проба 1]: если окажется узким местом (типичные размеры из пробы 6),
    альтернатива — локальный Bot API server (до 2000 МБ). Цена: отдельный контейнер
    `telegram-bot-api`, `api_id`/`api_hash` с my.telegram.org (новые секреты), `logOut` бота
    из облачного API (обратный переход — с ожиданием), том под файлы, ещё одна точка отказа.
    Не выбираем без проб.

## 3. Шаг 0 — пробы

Подробности, скрипты и результаты — notes, раздел «Пробы шага 0». Пробы идут с Mac через VPN;
результаты 4–6 — «проверено с VPN, на VPS перепроверить» (дымовые тесты этапа развёртывания).
Проект проб — отдельный uv-проект в scratchpad, не в репозитории.

Общее для скриптов с секретами: токен — только `os.environ["BOT_TOKEN"]`, cookies — путь из
`IG_COOKIES_FILE`; печатаются только тип исключения и его текст после `redact()` (вырезает токен,
шаблон токена, значения cookies — скрипт читает их в множество, но не печатает); без `repr`
исключений, без URL Telegram API; отправка — только в `OWNER_ID`. Вывод yt-dlp — после `scrub()`.

| # | Вопрос | Ответ — это… | Кто |
|---|---|---|---|
| 0 | Состояние бота: `getMe`, `getWebhookInfo`, один `getUpdates(timeout=1)` без `offset` | 409 или нет; счётчики типов апдейтов и чатов; ничего не меняет | пользователь, первой |
| 1 | Лимит размера отправки (видео ~49/51 МБ, фото ~9.5/10.5 МБ, фото 300×7000) | порог; класс исключения aiogram и текст; нужен ли откат фото → документ | пользователь |
| 2 | Медиагруппа: 10 (смешанная), 11, 1; подпись на 1-м/2-м | максимум, ошибки, видимость подписи | пользователь |
| 3 | Подпись 1024/1025 (ASCII, эмодзи, кириллица); текст 4096/4097 | лимит и единица счёта | пользователь |
| 4 | yt-dlp на Instagram: Reel, пост-фото, пост-видео, карусель; без cookies / с копией / с испорченным `sessionid` | ключи info dict; скачиваются ли фото; тексты ошибок логина; что yt-dlp делает с `cookiefile` (пары имя → хеш значения до/после) | без cookies — агент; с cookies — пользователь |
| 5 | yt-dlp на Shorts: формат, merge, кодеки, faststart, нужен ли JS-рантайм | селектор формата; FFmpeg; экстры yt-dlp | агент; воспроизведение — пользователь |
| 6 | Время и размеры (замеры в 4 и 5) | тайм-аут и запас до лимита | агент / пользователь |

Плюс `probe_send_media.py`: файлы из 4/5 владельцу — `sendVideo` с и без
`width/height/duration/supports_streaming`; пользователь смотрит превью, пропорции, воспроизведение.

**Выводы проб** (что поменялось в решениях): _заполняется после шага 0_.

## 4. Структура пакета

```
tg_bot/
  __init__.py
  __main__.py        # настройки → логирование → Bot, Dispatcher → start_polling
  config.py          # Settings, load_settings(environ), ConfigError
  links.py           # Link, Platform, PostKind; find_links(candidates) -> list[Link]
  routing.py         # реестр загрузчиков: Link -> Downloader (по Platform)
  downloader/
    __init__.py
    port.py          # Downloader (Protocol), MediaItem, DownloadResult, ошибки
    ytdlp.py         # общий код: опции, запуск в потоке, тайм-аут/отмена, перевод ошибок
    instagram.py     # InstagramDownloader
    shorts.py        # ShortsDownloader
  sending.py         # Send-типы, plan_sends(result, limits) — чистое; send(bot, chat_id, sends)
  limits.py          # константы Telegram со ссылкой на notes (пробы 1–3)
  pipeline.py        # handle_link: tmpdir → семафор → загрузка → отправка → очистка
  access.py          # is_allowed(update, owner_id) — этап 1: только личный чат владельца
  handlers.py        # aiogram Router, outer-middleware доступа, обработчик сообщений
tests/
  fakes.py           # FakeDownloader, FakeSession (aiogram)
  scrub.py           # очистка info dict для фикстур (решение 20a)
  test_links.py, test_routing.py, test_config.py, test_scrub.py, test_sending.py,
  test_pipeline.py, test_access.py, test_handlers.py, test_ytdlp_errors.py
  smoke/test_instagram.py, smoke/test_shorts.py
scripts/git-hooks/pre-commit, scripts/test-git-hooks.sh, .gitleaks.toml,
.github/workflows/ci.yml, .env.example (BOT_TOKEN, OWNER_ID, IG_COOKIES_FILE — без значений)
```

## 5. Интерфейс порта (черновик, без реализации)

```python
class Platform(StrEnum): INSTAGRAM = "instagram"; YOUTUBE = "youtube"
class PostKind(StrEnum): REEL = "reel"; POST = "post"; SHORTS = "shorts"

@dataclass(frozen=True, slots=True)
class Link:
    platform: Platform
    kind: PostKind
    media_id: str          # shortcode / video id
    url: str               # нормализованная каноническая ссылка

class MediaKind(StrEnum): PHOTO = "photo"; VIDEO = "video"

@dataclass(frozen=True, slots=True)
class MediaItem:
    kind: MediaKind
    path: Path             # внутри workdir
    width: int | None = None
    height: int | None = None
    duration: float | None = None      # секунды, для видео

@dataclass(frozen=True, slots=True)
class DownloadResult:
    items: tuple[MediaItem, ...]       # исходный порядок, не пусто
    caption: str                       # "" — описания нет

class Downloader(Protocol):
    async def download(self, link: Link, workdir: Path) -> DownloadResult: ...

class DownloadError(Exception):        # базовая; user_message — короткий текст пользователю
    user_message: ClassVar[str]
class MediaNotFound(DownloadError): ...      # удалено / не существует
class LoginRequired(DownloadError): ...      # нужен логин или cookies отвергнуты
class RateLimited(DownloadError): ...
class MediaTooLarge(DownloadError): ...
class DownloadTimeout(DownloadError): ...
class NoMedia(DownloadError): ...            # публикация без поддерживаемых медиа
class DownloadFailed(DownloadError): ...     # прочее (в т.ч. ошибка FFmpeg)

# routing.py
class DownloaderRegistry:
    def __init__(self, by_platform: Mapping[Platform, Downloader]) -> None: ...
    def for_link(self, link: Link) -> Downloader: ...   # KeyError не наружу: UnsupportedLink

# sending.py
@dataclass(frozen=True) class SendVideo: item: MediaItem; caption: str | None
@dataclass(frozen=True) class SendPhoto: item: MediaItem; caption: str | None
@dataclass(frozen=True) class SendAlbum: items: tuple[MediaItem, ...]; caption: str | None
@dataclass(frozen=True) class SendText: text: str
type Send = SendVideo | SendPhoto | SendAlbum | SendText
def plan_sends(result: DownloadResult, limits: TelegramLimits) -> list[Send]: ...
async def send(bot: Bot, chat_id: int, sends: Sequence[Send], reply_to: int | None) -> None: ...
```

Поля `MediaItem` (размеры, длительность) — уточняются пробой 5 (`probe_send_media.py`).

## 6. Части этапа (каждая — отдельная сессия и PR)

**Часть A — защита, CI, чистые слои** (ветка `stage-1-pipeline`, вместе с коммитом плана):
`.gitleaks.toml`, `scripts/git-hooks/pre-commit`, `scripts/test-git-hooks.sh` (+ в секцию `hooks`),
прогон `gitleaks git` по истории; `.github/workflows/ci.yml`; `.env.example`; `tg_bot/links.py`,
`tg_bot/config.py` + тесты; `pytest pythonpath`, маркер `smoke`; `CLAUDE.md` (Commands:
`core.hooksPath`, smoke; Architecture: модули). Зависимостей бота ещё нет. Порядок коммитов:
хук gitleaks включён и проверен — автотест и положительный контроль пользователя на `.env` и
`data/instagram.txt` — **до** `config.py` (первый код с токеном).

**Часть B — порт и загрузчики** (ветка `stage-1-pipeline-b` от master после слияния A):
`uv add` aiogram и yt-dlp (решение 17); `downloader/` (port, ytdlp, instagram, shorts);
`routing.py`; `tests/fakes.py` (FakeDownloader); `tests/scrub.py` с тестом (решение 20a);
юнит-тесты перевода ошибок и разбора info dict на фикстурах из проб; дымовые тесты; аудит
читателей info dict (таблица в notes).

**Часть C — отправка, обработчики, запуск** (ветка `stage-1-pipeline-c`): `limits.py`,
`sending.py`, `pipeline.py`, `access.py`, `handlers.py`, `__main__.py`; FakeSession; ручная
проверка в личном чате; `/finish-stage` на PR части C.

## 7. Сводка «пункт → тесты»

| Пункт | Юнит (фейк) | Дымовой | Ручная |
|---|---|---|---|
| Разбор ссылок (все варианты ТЗ 3.1, 2.1–2.3; не-ссылки, `youtu.be`, stories, обычное видео YouTube) | `test_links` таблицей, отрицательные с положительным контролем | — | — |
| Конфигурация: обязательные, неверный `OWNER_ID`, токен не в `repr`/сообщениях | `test_config` | — | — |
| Маршрутизация, неподдерживаемая платформа → отказ | `test_routing` | — | — |
| Перевод ошибок yt-dlp → ошибки порта; разбор `entries`/одиночного; незнакомые ключи и `None` → пропуск с логом | `test_ytdlp_errors`, `test_instagram_info` на фикстурах проб | — | — |
| Reel, пост-фото, пост-видео, карусель (смешанная), длинное описание (ТЗ 11.2) | — | `smoke/test_instagram` (с cookies — пользователь) | отправка в чат |
| Shorts обычный, раздельные потоки + merge, недоступный ролик | — | `smoke/test_shorts` | воспроизведение в Telegram |
| Ошибка авторизации Instagram → сообщение владельцу | `test_pipeline` (фейк `LoginRequired`) | испорченный cookies (пользователь) | — |
| Разбиение медиагрупп, порядок, одиночный элемент | `test_sending` (`plan_sends`) | — | карусель > лимита, если найдётся |
| Подпись: в лимите, сверх лимита → отдельное сообщение, разбиение текста, plain text | `test_sending` | — | — |
| Временный каталог удаляется при успехе, ошибке загрузки, ошибке отправки, отмене | `test_pipeline` | — | — |
| Тайм-аут загрузки; семафор освобождается на всех путях; повтор ссылки в работе | `test_pipeline` | — | — |
| Ошибка одной ссылки не мешает другим в сообщении; `CancelledError` не глотается | `test_pipeline`/`test_handlers` | — | — |
| Доступ: личный чат владельца обрабатывается; группа, чужой личный чат, канал — пропуск с логом | `test_access`, `test_handlers` (с положительным контролем) | — | сообщение в старой группе — бот молчит, лог есть |
| Незнакомый апдейт, пустой `text`/`caption`, `text_link` | `test_handlers` | — | — |
| Ошибка Telegram при отправке → лог, бот жив | `test_handlers` (FakeSession отвечает ошибкой) | — | — |
| gitleaks: токен (оба формата), cookies Netscape и `sessionid=` блокируют коммит; без секрета — проходит; без gitleaks — блок | `scripts/test-git-hooks.sh` (секция hooks) | — | `gitleaks dir --redact -v` на `.env` и `data/instagram.txt` — ≥1 находка в каждом (пользователь) |
| Очистка info dict: `cookies`, `http_headers` на любой глубине, подписанные URL | `test_scrub` (с положительным контролем) | — | — |
| CI | — | — | первый PR зелёный; намеренно красный lint-коммит в ветке → красный CI (положительный контроль), откат |
| Логи без секретов | `test_config`, ревью | — | просмотр лога ручного прогона |

Без автотеста — с причиной: воспроизведение видео в Telegram (только глазами); поведение на VPS
(этап развёртывания).

## 8. Риски

- **Instagram на VPS ведёт себя иначе** (логин-стена, 429, checkpoint для аккаунта): на этапе
  развёртывания дымовые тесты на VPS до включения бота; варианты — cookies обязательны, снижение
  частоты запросов, прокси (опция yt-dlp `proxy` из переменной окружения; цена — платный
  резидентный прокси и ещё один секрет). Решение — по результату дымовых тестов на VPS.
- **yt-dlp не отдаёт фото Instagram** — решение 6, выбор после пробы 4; может сдвинуть часть B.
- **YouTube требует JS-рантайм / PO-token, или блокирует IP дата-центра** («Sign in to confirm
  you're not a bot»): проба 5 на Mac покажет первое; второе — только на VPS (cookies отдельного
  аккаунта Google или прокси; решение на этапе развёртывания).
- **Лимит 50 МБ узкий** — решение 21.
- **`scripts/test-hooks.sh` написан под BSD/bash 3.2 и может упасть на Linux в CI**: чинить
  переносимостью; если не выходит — job `hooks` на `macos-latest` (дороже по минутам).
- **409 Conflict в пробе 0** — где-то жив другой экземпляр со старым токеном: стоп, решение за
  пользователем (`revoke` токена в BotFather = новый токен; бот остаётся в группах).
- **Поток yt-dlp не прерывается мгновенно** — решение 8; если progress-hook не прерывает
  извлечение (не загрузку), ждём поток с ограничением и пишем в лог.
- **Cookies в фикстурах или выводе проб**: yt-dlp кладёт их в `formats[].cookies` и
  `http_headers`. Нужны **обе меры вместе**: `scrub()` (решение 20a) убирает их механически до
  записи — но работает только по известным ключам и пропустит новое поле yt-dlp; правило gitleaks
  на `sessionid` (решение 18) ловит значение в любом месте — но только при коммите и только по
  шаблону. Одна мера без другой оставляет дыру.
- **Шаблон токена не подходит к настоящему токену / cookies не ловятся** — положительный контроль
  на настоящих `.env` и `data/instagram.txt` (решение 18) до первого кода с токеном.
- **yt-dlp меняет формат ошибок** — перевод ошибок опирается на тексты из проб; незнакомая ошибка →
  `DownloadFailed` (общий текст пользователю), дымовые тесты ловят смену формата.

## 9. Дальше (не входит в этап 1)

- Группы: запросы владельцу, разрешение/отказ, тайм-аут 60 с, выход из неразрешённых, JSON-хранилище в `data/` (ТЗ 4.2–4.4).
- Уведомление владельцу об истёкших cookies с ограничением частоты (ТЗ 7.3) — вместе с группами.
- Docker, compose, развёртывание на VPS, резервная копия `data/` и `.env` (ТЗ 8).
- Дымовые тесты по расписанию с отчётом владельцу (ТЗ 11).
- Административные команды (ТЗ 4.5).

## 10. Проверка (Definition of done этапа)

`scripts/check.sh` код 0 через `check-runner` (lint, hooks с git-hook тестом, test); CI зелёный на
каждом PR; таблицы зубов по частям в notes; `py-reviewer` на diff каждой части; ручной прогон в
личном чате: Reel, пост-фото, пост-видео, карусель, Shorts, длинное описание, недоступная ссылка;
сообщение в старой группе — бот молчит, в логе запись; отклонения — в план.
