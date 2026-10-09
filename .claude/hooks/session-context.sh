#!/bin/bash
# SessionStart: короткая сводка состояния репозитория в контекст сессии (stdout, до 10 строк).
# Ветка, отставание/опережение origin/master (только по локальным ссылкам, без `git fetch`),
# незакоммиченные файлы, последний план этапа без строки «Статус: выполнен».
# Никогда не блокирует старт сессии: любая ошибка git — просто пропуск строки.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-$PWD}"
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

git_() { git --no-optional-locks "$@"; }

branch=$(git_ branch --show-current 2>/dev/null || true)
[[ -n "$branch" ]] || branch="(detached at $(git_ rev-parse --short HEAD 2>/dev/null || echo '?'))"
echo "Branch: $branch"

if git_ rev-parse --verify -q refs/remotes/origin/master >/dev/null 2>&1; then
    counts=$(git_ rev-list --left-right --count origin/master...HEAD 2>/dev/null || true)
    if [[ -n "$counts" ]]; then
        read -r behind ahead <<<"$counts"
        echo "vs origin/master (local ref, no fetch): $behind behind, $ahead ahead"
    fi
else
    echo "origin/master: no local ref"
fi

changed=$(git_ status --porcelain 2>/dev/null || true)
if [[ -n "$changed" ]]; then
    total=$(wc -l <<<"$changed" | tr -d ' ')
    echo "Uncommitted: $total file(s)"
    # Первые пять имён, остальное — счётчиком, чтобы уложиться в лимит строк.
    sed -E 's/^.{3}/  /' <<<"$changed" | head -n 5
    ((total <= 5)) || echo "  … and $((total - 5)) more"
else
    echo "Uncommitted: none"
fi

# Последний по номеру этапа план: старые планы без отметки не считаются.
# Файлы заметок (`stage-N-имя-notes.md`, второй уровень плана) — не планы: отметки в них нет.
plan=$(ls docs/plans/stage-*.md 2>/dev/null | grep -v -- '-notes\.md$' | sort -V | tail -n 1 || true)
if [[ -n "$plan" ]] && ! grep -q 'Статус: выполнен' "$plan"; then
    echo "Plan not marked done: $plan"
fi
