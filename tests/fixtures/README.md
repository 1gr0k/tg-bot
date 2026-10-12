# Фикстуры info dict yt-dlp

Снято 2026-10-13, yt-dlp 2026.08.19, **без cookies** (Mac, VPN); ссылки — из notes плана этапа 1.
Каждый файл прошёл `tests/scrub.py` (без `cookies`/`http_headers`, подписанные URL —
`<signed-url>`, у картинок остаётся только `?stp=…` — маркер размера). Проверено: `gitleaks dir
--redact` — находок нет; в файлах нет IP, `sig=`, `googlevideo`.

Что в них нарочно изменено или убрано (бот этого не читает):

- Аккаунты и имена (`uploader`, `channel`, `uploader_id`, …) — `user`; `@упоминания` в описаниях и
  заголовках — `@user`.
- Убраны `comments`, `automatic_captions`, `heatmap`, `chapters`, `tags` и прочий шум.
- `instagram_reel.json`: из `thumbnails` убраны повторяющиеся записи.
- `youtube_shorts_long.json`: оставлены только читаемые ботом и нужные выбору формата ключи; в
  `formats` — `mp4`/`m4a` по `https` (без HLS).

| Файл | Источник | Как снят |
|---|---|---|
| `instagram_reel.json` | `/reel/DeRrI8_BTZz/` (Reel со звуком, 73 с) | `extract_info(download=False, process=False)`, `ignore_no_formats_error`; нет `format_id` у прогрессивных форматов (его проставляет `process_ie_result`) |
| `instagram_photo_post.json` | `/p/DdjcJv8Auv4/` (одно фото) | то же; `formats: []`, 14 `thumbnails`, последний без маркера — оригинал |
| `instagram_mixed_carousel.json` | `/p/DeEjFYODJXW/` (фото + видео) | то же; `_type: playlist`, `entries` |
| `youtube_shorts_long.json` | `/shorts/ZoAPORyKG4A` (135 с) | `extract_info(download=False)` с селектором бюджета (решение 9) и `format_sort` (решение 5) |
| `instagram_reel_with_cookies_manual.json` | — | **собрано вручную по notes** (проба 4 с cookies): поля, которые бывают только при прогоне с cookies |
