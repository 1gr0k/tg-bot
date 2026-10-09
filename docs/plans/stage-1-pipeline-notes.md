# Этап 1 — notes

Рабочие записи к [`stage-1-pipeline.md`](stage-1-pipeline.md): пробы шага 0, тестовые ссылки,
таблицы проверки на зубы, аудит читателей, итоги частей. Выводы, меняющие решения, — в план.

## Окружение проб

- Дата: 2026-10-10. Машина: Mac (arm64), сеть — через VPN. **Результаты 4–6 — «проверено с VPN,
  на VPS перепроверить»** (дымовые тесты этапа развёртывания).
- Проект проб — отдельный uv-проект в scratchpad сессии (`<scratchpad>/probes/`), не в репозитории.
- Python 3.12.15, uv 0.12.24, **yt-dlp 2026.08.19**, **aiogram 3.31.0** (→ нижние границы в
  `pyproject.toml`, решение 17), ffmpeg/ffprobe 9.0.2 (brew; в Docker-образе будет своя версия),
  gitleaks 8.30.1.
- `yt-dlp-ejs` с голым `yt-dlp` не ставится; JS-рантайма (deno) на Mac нет.

## Тестовые ссылки

Кандидаты из `_TESTS` экстракторов yt-dlp 2026.08.19 (на подтверждение пользователю):

| Тип | Ссылка | Источник | Статус без cookies |
|---|---|---|---|
| Reel | `https://www.instagram.com/reel/Chunk8-jurw/` | `instagram.py`, «reel» | скачан; **звука нет** (похоже, ролик без звука) |
| Пост-карусель (3 видео) | `https://www.instagram.com/p/BQ0eAlwhDrw/` | «multi video post» | скачан |
| Пост-видео | `https://www.instagram.com/p/BkfuX9UB-eK/` (шорткод IGTV через `/p/`) | «IGTV» | ошибка «empty media response» |
| Shorts | `https://www.youtube.com/shorts/BGQWPY4IigY` | `youtube/_video.py` | скачан |
| Shorts | `https://www.youtube.com/shorts/18NGQq7p3LY` | `youtube/_video.py` | скачан |
| Shorts недоступный | `https://www.youtube.com/shorts/xxxxxxxxxxx` | выдуман | «This video is unavailable» |

От пользователя (публичные, 2026-10-10; параметры `?cplk=`, `?rpxt=`, `?dlrf=`, `?is=` — как
пришли, заодно проверка разбора ссылок):

| Тип | Ссылка | Статус без cookies |
|---|---|---|
| Reel со звуком, 73 с | `https://www.instagram.com/reel/DeRrI8_BTZz/?cplk=N3hxY3JnM2E0cWpw` | скачан (со звуком) |
| Пост с одной фотографией | `https://www.instagram.com/p/DdjcJv8Auv4/?rpxt=MWhndGs0dmgzNGkweQ==` | по умолчанию — ошибка; с `ignore_no_formats_error` — скачан |
| Смешанная карусель (фото + видео) | `https://www.instagram.com/p/DeEjFYODJXW/?dlrf=MXYweGxybHh4anVjNQ==` | по умолчанию — ошибка; с `ignore_no_formats_error` — скачана |
| Shorts 135 с | `https://youtube.com/shorts/ZoAPORyKG4A?is=NI-y_uCsEB4XJ61B` | скачан |

Нет у пользователя: пост с одним видео (ТЗ 11.2 — открыт, ссылка для дымового теста ищется),
описание > 1024 символов, карусель > 10 элементов — разбиение и длинная подпись проверяются
юнит-тестами и пробами 2–3 на сгенерированных данных.

## Пробы шага 0

### 0. Состояние бота

Запуск — пользователь (`probe_0_bot_state.py`, 2026-10-10); вывод проверен пользователем — секретов
нет. Первый запуск упал с 404: в `.env` после токена стояла запятая (исправлено пользователем) →
решение 16 (проверка формата при старте).

```
getMe: @insta_for_beer_bot; can_join_groups=True; can_read_all_group_messages=True
getWebhookInfo: webhook задан=False; pending_update_count=149; last_error_date=нет; allowed_updates=['message', 'callback_query', 'my_chat_member']
getUpdates: получено 100 (offset не сдвигался, апдейты не подтверждены)
  типы апдейтов: {'message': 100}
  типы чатов: {'supergroup': 100}; разных чатов: 3; из личного чата владельца: 0
  возраст: самый старый 23.8 ч, самый новый 9.1 ч
```

Выводы:
- **409 нет** — другого экземпляра на этом токене нет; webhook не задан, long polling возможен.
- **Privacy mode выключен** (`can_read_all_group_messages=True`): бот получает все сообщения
  в группах → решение 1 (журнал проигнорированного: `DEBUG`, `INFO` раз на чат, без текста).
- Накопилось 149 апдейтов, в выборке 100 — только `message` из 3 супергрупп прошлой версии,
  возраст 9–24 ч (Telegram хранит до 24 ч) → решение 2: `drop_pending_updates=True`.
- На стороне Telegram сохранён `allowed_updates` прошлой версии → решение 2: передавать явно.

### 1. Лимит размера отправки

### 2. Медиагруппа

### 3. Подпись и текст

### 4. yt-dlp на Instagram

Скрипт: `probe_4_instagram.py --mode none|copy|broken [--format F] URL…`. Без cookies — запуск
агентом (Mac, VPN). С cookies — ждёт пользователя.

**Без cookies** (`--mode none`):

- Каждая ссылка — предупреждение `[Instagram] <code>: No CSRF token set by Instagram API`, но
  загрузка идёт.
- Reel `Chunk8-jurw`: `_type=video`, 9 форматов. 6 DASH-форматов **только видео**
  (`dash-…v`, `avc1.64001F`, `acodec=none`, до 720×1280) и 3 формата `0/1/2` с неизвестным
  `vcodec` (`null`) и `acodec=none` (по ffprobe — тоже без звука, 480×854). Отдельного аудиопотока
  нет. По умолчанию выбран `dash-…v` 720×1280. Файл 1.95 МБ, H.264, faststart, **без звука**,
  3.8 с. `duration=None` в info, `width/height` есть. `description` — 373 символа.
- Карусель `BQ0eAlwhDrw`: `_type=playlist`, `entries` 3 (все видео; у каждого свой `id`,
  4 формата, 12 thumbnails). `description` — на верхнем уровне (1012 символов), у entries нет.
  Файлы — `<playlist_index>_<id>.mp4` в исходном порядке; 640×640 H.264, faststart, без звука.
  5.1 с.
- `p/BkfuX9UB-eK`: `DownloadError: ERROR: [Instagram] BkfuX9UB-eK: Instagram sent an empty media
  response. Check if this post is accessible in your browser without being logged-in. If it is
  not, then use --cookies-from-browser or --cookies…` — так выглядит «нужен логин / недоступно»
  без cookies. Отличить «нужен логин» от «удалено» по этому тексту нельзя; проверить с cookies.
- Ключи info dict верхнего уровня включают `http_headers` → `scrub()` нужен (решение 20a).
  Проверено: в сохранённых `info.json` нет `http_headers`, `cookies`, `sessionid`; подписанные URL
  заменены (227 заглушек) — положительный контроль: в сыром info dict `http_headers` был.

**Ссылки пользователя, без cookies** (2026-10-10):

- **Reel со звуком `DeRrI8_BTZz`** (73 с). По умолчанию yt-dlp: `dash-…v` (**VP9**, 1080×1920) +
  `dash-…a` (AAC) с merge → 9.87 МБ, **VP9 в MP4**. Все DASH-видео этого Reel — VP9 (540–1080p).
  Прогрессивные `1/2/3` (кодеки в info — `null`) по ffprobe — **H.264 720×1280 + AAC, со звуком,
  faststart**, 13.81 МБ, без merge, ~4 с.
- **Селектор для Instagram: `b[format_id!*=dash]/bv*[vcodec^=avc1]+ba/bv*+ba/b`** —
  прогрессивный H.264 в первую очередь. Проверен на `DeRrI8_BTZz` (формат 3, H.264+AAC),
  `Chunk8-jurw` (формат 2, H.264 без звука — у ролика его нет), карусели `BQ0eAlwhDrw` (формат 2
  у всех элементов). Отвергнуто: выбор yt-dlp по умолчанию (VP9; воспроизведение VP9 в Telegram
  на всех клиентах не гарантировано — проверяется `probe_send_media.py`).
- У прогрессивных форматов **`width`, `height`, `duration`, `vcodec`, `acodec` в info — `None`**.
  Размеры и длительность для `MediaItem` — из ffprobe по скачанному файлу, не из info dict.
- **Фото yt-dlp по умолчанию не скачивает**: пост-фото `DdjcJv8Auv4` → `DownloadError: ERROR:
  [Instagram] DdjcJv8Auv4: There is no video in this post`; смешанная карусель `DeEjFYODJXW` падает
  **целиком** на фото-элементе: `ERROR: [Instagram] DeEi5N8sZfR: No video formats found!; please
  report this issue…`.
- **Обход внутри yt-dlp** (`probe_4c_photos.py`, `probe_4d_thumbsizes.py`): опция
  `ignore_no_formats_error=True` превращает ошибку экстрактора в предупреждение (код
  `raise_no_formats` в `extractor/common.py`); `extract_info(download=False, process=False)`
  отдаёт фото как элемент с пустыми `formats` и 14 `thumbnails` (из `image_versions2`).
  - `thumbnails` — **без `width/height`**; размер — только в параметре URL `stp`
    (`…_s150x150_…` … `…_p1080x1080_…`). **Последний thumbnail — без маркера размера — оригинал**:
    3072×4096 (1.69 МБ) и 2092×2789 (0.74 МБ, `dst-jpegr`). Выбор «наибольший по width×height»
    не работает (все `None` → взят первый, 150×150).
  - Загрузка картинки — `ydl.urlopen(url)` (сеть, заголовки и cookies yt-dlp).
  - Смешанная карусель: 2 элемента в исходном порядке — фото (`DeEi5N8sZfR`) и видео
    (`DeEi5dYjHRK`, 7 форматов, H.264 720×960, 2.50 МБ, без звука); `description` — 209 символов.
  - Вывод для решения 6: вариант (а) реализуется **внутри yt-dlp**, без aiohttp и без gallery-dl:
    фото — элемент с пустыми `formats`, URL — thumbnail без маркера размера в `stp` (запасной —
    последний в списке), скачивание — `ydl.urlopen`; видео — `process_ie_result` с селектором выше.

**Открыто:** с cookies, испорченный `sessionid`, поведение `cookiefile`, «нужен логин» против
«удалено» — у пользователя. Воспроизведение VP9 против H.264 в Telegram — `probe_send_media.py`.

### 5. yt-dlp на Shorts

Скрипт: `probe_5_shorts.py [--sort] URL…` (агент, без секретов).

- **По умолчанию yt-dlp выбирает AV1/VP9 + Opus в webm** (`398+251`, `401+251`) — для Telegram
  не годится, свой выбор формата обязателен.
- Фильтр `bv*[vcodec^=avc1][height<=1080][ext=mp4]+ba[acodec^=mp4a]/…` **неверен для вертикального
  видео**: `height` — длинная сторона, у `18NGQq7p3LY` выбрался 720×988, хотя есть 1080×1482.
- **`format: "bv*+ba/b"` + `format_sort: ["vcodec:h264", "acodec:aac", "res:1080", "ext:mp4:m4a"]`**
  (`res` в yt-dlp — меньшая сторона кадра): `137+140` 608×1080 и `299+140` 1080×1482, H.264 +
  AAC, **merge через FFmpeg** (`[Merger]`), результат **faststart=True** без перекодировки.
- Предупреждение на каждый запрос: `No supported JavaScript runtime could be found. Only deno is
  enabled by default… YouTube extraction without a JS runtime has been deprecated, and some formats
  may be missing.` Сейчас форматы есть, но это уже устаревший путь → решение 17 / Docker (deno +
  `yt-dlp[default]`).
- Недоступный ролик: `DownloadError: ERROR: [youtube] <id>: This video is unavailable`.
- Название — `title`; `description` бывает пустым (0 и 26 символов у тестовых).

**Shorts 135 с `ZoAPORyKG4A`** (ссылка пользователя):

- С `bv*+ba/b` + `format_sort`: `137+140-1`, H.264 1080×1920 + AAC, **58.44 МБ — больше
  ожидаемого лимита 50 МБ**. За 8.9 с.
- Две аудиодорожки: `140-0` — `en-US`, `language_preference=-1` («English (US)», дубляж) и
  `140-1` — `ru`, `language_preference=10` («Russian original (default)»). Выбрана оригинальная:
  поле `lang` в сортировке yt-dlp по умолчанию идёт после наших полей и решает при равенстве.
- **Бюджет размера в селекторе**: `bv*[filesize<45M]+ba/b[filesize<48M]` + тот же `format_sort`
  → `136+140-1`, H.264 720×1280 + AAC (оригинал), **19.18 МБ**, 4.8 с; у коротких Shorts бюджет
  ничего не меняет (`18NGQq7p3LY` — тот же 1080×1482). Пороги 45/48 МБ — черновые, точные — после
  пробы 1. У YouTube `filesize` точный; у прогрессивных форматов Instagram `filesize` нет —
  там фильтр `[filesize<?…]` пропускает, страховка — `max_filesize` и проверка размера файла.

### 6. Время и размеры

| Ссылка | Длительность | Размер | Время загрузки |
|---|---|---|---|
| Reel `Chunk8-jurw` (720×1280) | 5 с | 1.95 МБ | 3.8 с |
| Карусель `BQ0eAlwhDrw` (3 видео 640×640) | 4 + 32 + 6.5 с | 0.04 + 2.52 + 0.96 МБ | 5.1 с |
| Shorts `BGQWPY4IigY` (608×1080) | 14 с | 2.02 МБ | 3.0 с |
| Shorts `18NGQq7p3LY` (1080×1482) | 3.4 с | 1.95 МБ | 2.9 с |
| Reel `DeRrI8_BTZz`, прогрессивный H.264 720×1280 | 73 с | 13.81 МБ | ~4 с |
| Reel `DeRrI8_BTZz`, по умолчанию VP9 1080×1920 + merge | 73 с | 9.87 МБ | 4.5 с |
| Пост-фото `DdjcJv8Auv4` (оригинал 3072×4096) | — | 1.69 МБ | 2.7 с |
| Карусель `DeEjFYODJXW` (фото + видео 720×960) | 16 с | 0.74 + 2.50 МБ | 4.6 с |
| Shorts `ZoAPORyKG4A` 1080×1920 | 135 с | **58.44 МБ** | 8.9 с |
| Shorts `ZoAPORyKG4A` 720×1280 (бюджет размера) | 135 с | 19.18 МБ | 4.8 с |

Выводы: загрузка — 3–9 с с Mac/VPN; 1080p H.264 ≈ 0.43 МБ/с видео → **Shorts 1080p длиннее
~110 с не влезают в 50 МБ**, бюджет размера в селекторе обязателен. Тайм-аут загрузки — с запасом
на VPS (порядок минуты, точное значение — в части B, с перепроверкой на VPS).

### Отправка результатов 4/5 (воспроизведение)

## Аудит читателей

_Часть B: читатели info dict yt-dlp; часть C: читатели Update._

## Положительный контроль gitleaks на настоящих секретах

## Зубы: часть A

## Зубы: часть B

## Зубы: часть C

## Итог части A

## Итог части B

## Итог части C
