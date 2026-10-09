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

**Нужны от пользователя** (в `_TESTS` нет): Reel со звуком, пост с одной фотографией, пост с
одним видео, смешанная карусель (фото + видео), пост с длинным описанием (> 1024 символов),
карусель > 10 элементов (если найдётся).

## Пробы шага 0

### 0. Состояние бота

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

**Открыто:** звук — у тестовых роликов его, похоже, просто нет; нужен Reel со звуком, чтобы понять,
есть ли отдельный DASH-аудиопоток и нужен ли селектор `bv*+ba/b` с merge. Фото, смешанная
карусель — нужны ссылки. С cookies, испорченный `sessionid`, поведение `cookiefile` — у пользователя.

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

### 6. Время и размеры

| Ссылка | Длительность | Размер | Время загрузки |
|---|---|---|---|
| Reel `Chunk8-jurw` (720×1280) | 5 с | 1.95 МБ | 3.8 с |
| Карусель `BQ0eAlwhDrw` (3 видео 640×640) | 4 + 32 + 6.5 с | 0.04 + 2.52 + 0.96 МБ | 5.1 с |
| Shorts `BGQWPY4IigY` (608×1080) | 14 с | 2.02 МБ | 3.0 с |
| Shorts `18NGQq7p3LY` (1080×1482) | 3.4 с | 1.95 МБ | 2.9 с |

Ориентир: ≈ 0.15–0.6 МБ на секунду видео; до лимита 50 МБ далеко для коротких роликов. Нужны
замеры на длинных Reels/Shorts (до 3 минут) — ссылки пользователя.

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
