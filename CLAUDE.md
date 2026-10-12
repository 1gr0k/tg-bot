# tg-bot

Telegram-бот: скачивает медиа по ссылкам на Instagram (Reels, посты, карусели) и YouTube Shorts и
отправляет их в разрешённые группы. ТЗ — `docs/spec.md` (читать по разделам: `grep -n '^#'`).
Разработка с нуля; код бота — по плану этапа 1 (`docs/plans/`), сейчас готовы `links`, `config`, `routing` и `downloader/`.

## Toolchain

- Python 3.12 (`.python-version`), окружение и зависимости — `uv`, `uv.lock` в git.
- `ruff` (формат и линт, правила в `pyproject.toml`) и `pytest` — в dev-группе, только через `uv run`.
- Стек бота (с этапа 1): aiogram 3, yt-dlp как библиотека, FFmpeg. Развёртывание — Docker compose.
- macOS: `/bin/bash` 3.2 и BSD-утилиты — скрипты и хуки пишутся под них.
- Разовые команды в сессии выполняет zsh (нет `PIPESTATUS`, нет разбиения слов), скрипты — `#!/bin/bash`.

## Commands

- `uv sync` — окружение по `uv.lock`.
- `scripts/check.sh` — все проверки; `--only lint|hooks|test`; `--test <pytest node id>`.
  Логи — `.build/check-logs/`. Код pytest 5 («тесты не найдены») — провал.
- Секция `hooks` — `scripts/test-hooks.sh`, кейсы хуков из `.claude/hooks/`. Проверка хука на
  зубы — только на копии: `HOOKS_DIR=<scratchpad>/hooks scripts/test-hooks.sh`.
- Новый клон: `git config core.hooksPath scripts/git-hooks` — pre-commit с gitleaks (fail-closed:
  без `gitleaks` в PATH коммит отклоняется; `brew install gitleaks`). Обход (`--no-verify`, `-n`,
  `-c core.hooksPath=…`) блокирует PreToolUse-хук `.claude/hooks/deny-hook-bypass.sh` — это
  защита от привычки; гарантия — job `gitleaks` в CI по всей истории. Секция `hooks` включает и
  `scripts/test-git-hooks.sh`; проверка на зубы — на копиях: `GITLEAKS_CONFIG_SRC=<копия
  .gitleaks.toml>`, `GIT_HOOKS_SRC=<копия каталога>`.
- Дымовые тесты загрузчиков (`tests/smoke/`, реальная сеть, Instagram с cookies — запускает
  пользователь): `uv run pytest -m smoke`. По умолчанию исключены (`-m 'not smoke'`), в CI не идут.

## Architecture

Слои (ТЗ 6.2); пакет — плоская `tg_bot/`, реализовано пока `links`, `config`, `routing`, `downloader/`:

- **Разбор ссылок** (`tg_bot/links.py`) — поиск и нормализация ссылок в тексте; чистые функции,
  без сети. `find_links(candidates) -> list[Link]`; `/p/<код>` не определяет тип публикации.
- **Маршрутизация** (`tg_bot/routing.py`) — `DownloaderRegistry.for_link`: платформа → загрузчик,
  нет загрузчика — `UnsupportedLink`; тип публикации (`/p/` — фото, карусель или видео)
  определяет загрузчик по ответу yt-dlp, а не маршрутизация.
- **Загрузчик (порт)** (`tg_bot/downloader/`) — `port.py`: `Downloader`, `MediaItem`,
  `DownloadResult`, ошибки `DownloadError` (без типов yt-dlp и aiogram); `ytdlp.py`: общее —
  `ThreadRunner` (свой пул + семафор, тайм-аут, флаг отмены, ожидание потока до удаления каталога),
  бюджет размера, копия cookies, перевод ошибок, ffprobe; `instagram.py`, `shorts.py` — адаптеры.
  Остальной код зависит только от порта; в юнит-тестах — `tests/fakes.py` (`FakeDownloader`) и
  `tests/fake_ytdlp.py` (фейковый `YoutubeDL`); фикстуры info dict — `tests/fixtures/`
  (получать только через `tests/scrub.py`).
- **Отправка** — результат загрузки → вызовы Telegram (видео, фото, медиагруппы, подпись).
- **Обработчики aiogram** — тонкий слой: апдейт → проверка доступа → слои выше → ошибка пользователю.
- **Доступ и хранилище** — разрешённые группы и запросы (JSON в `data/`, атомарная запись).
- **Конфигурация** (`tg_bot/config.py`) — переменные окружения, проверка при старте:
  `load_settings(environ) -> Settings`, `ConfigError` с именем переменной без значения; токен не
  в `repr` и не в ошибках.

## Rules

- В `master` — только через PR; force-push и push в `master` запрещены.
- Файлы создаются и правятся только Write/Edit; запись через перенаправления shell блокирует хук.
- Защиту (deny, ask, хуки) не ослаблять даже временно; проба требует доступа в `.env` или `data/` —
  стоп: опиши, какие файлы нужны, их создаёт пользователь.
- `.env` и `data/` не читать (секреты и состояние бота; запрещено в `settings.json`).
  Значения секретов и cookies — никогда в логи.
- yt-dlp — только как библиотека, не внешней командой.
- Блокирующее (yt-dlp, FFmpeg, файловый ввод-вывод) в async-коде — через `asyncio.to_thread`.
- Временный каталог на каждую загрузку; удаление — в `try/finally` при любом исходе.
- Широкий `except` — только на границе запроса, с `# noqa: BLE001 — граница запроса`.
- Ошибка одного запроса не останавливает бота; внешние данные (Update, ответы yt-dlp, `data/`)
  читаются терпимо: незнакомое — пропуск с логом.
- Лимиты Telegram и поведение yt-dlp — только из проб (шаг 0 плана), не по памяти.
- Проверки запускает субагент `check-runner`; решение — по коду возврата.
- Новая фича — по скиллу `new-feature`; завершение этапа — `/finish-stage`.

## Definition of done

1. `scripts/check.sh` — код 0 (через `check-runner`; включает lint, hooks и test).
2. Новые тесты прошли проверку на зубы (таблица в notes плана).
3. `py-reviewer` на diff; замечания исправлены или записаны в план с причиной.
4. Отклонения от плана внесены в план.
