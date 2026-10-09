#!/bin/bash
# Кейсы хуков из .claude/hooks: вход хука → ожидаемый код возврата (и, где важно, результат).
# Падает (exit 1), если провален хоть один кейс.
#
# HOOKS_DIR — каталог проверяемых хуков (по умолчанию .claude/hooks). Проверка на зубы — только
# на копии: `HOOKS_DIR=<scratchpad>/hooks scripts/test-hooks.sh`. Мутация живого хука отключает
# защиту в самой сессии (deny-shell-writes перестаёт блокировать запись).
#
# Временные .py для format-python лежат в каталоге .test-hooks.* внутри проекта (вне проекта хук
# их пропускает). Каталог в .gitignore и удаляется при выходе; ruff с явным путём .gitignore не
# учитывает (проверено), поэтому хук эти файлы обрабатывает.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd -P)
hooks=$(cd "${HOOKS_DIR:-$root/.claude/hooks}" && pwd -P)
echo "hooks: $hooks"
work=$(mktemp -d "${TMPDIR:-/tmp}/test-hooks.XXXXXX")
work=$(cd "$work" && pwd -P)
inproj=$(mktemp -d "$root/.test-hooks.XXXXXX")
trap 'rm -rf "$work" "$inproj"' EXIT

passed=0
failed=0
rc=0
out=""

# run_hook <hook> <json> [env…] — запускает хук; результат в $rc и $out (stdout+stderr).
run_hook() {
    local hook=$1 json=$2
    shift 2
    set +e
    out=$(env CLAUDE_PROJECT_DIR="$root" "$@" "$hooks/$hook" <<<"$json" 2>&1)
    rc=$?
    set -e
}

# expect <имя кейса> <ожидаемый rc> [условие…] — условие — команда, которая должна пройти.
expect() {
    local name=$1 want=$2
    shift 2
    if [[ "$rc" != "$want" ]]; then
        echo "FAIL $name: exit $rc, expected $want"
        [[ -z "$out" ]] || sed 's/^/     | /' <<<"$out"
        failed=$((failed + 1))
    elif (($# > 0)) && ! "$@"; then
        echo "FAIL $name: exit $rc as expected, but check failed: $*"
        [[ -z "$out" ]] || sed 's/^/     | /' <<<"$out"
        failed=$((failed + 1))
    else
        echo "ok   $name"
        passed=$((passed + 1))
    fi
}

file_is() { [[ "$(cat "$1")" == "$2" ]]; }
out_has() { grep -qE -- "$1" <<<"$out"; }

bash_json() { jq -n --arg c "$1" --arg d "$root" '{tool_input: {command: $c}, cwd: $d}'; }
edit_json() { jq -n --arg f "$1" '{tool_input: {file_path: $f}}'; }

# --- deny-shell-writes.sh ---------------------------------------------------------------
heredoc_cmd='cat <<EOF > notes.txt
hello
EOF'
for case in \
    "block >|2|echo x > notes.txt" \
    "block >>|2|echo x >> notes.txt" \
    "block tee|2|echo x | tee notes.txt" \
    "block heredoc|2|$heredoc_cmd" \
    "allow /tmp|0|echo x > /tmp/test-hooks-probe.txt" \
    "allow 2>&1|0|ls 2>&1" \
    "allow /dev/null|0|ls > /dev/null" \
    'allow quoted >|0|echo "a > b"'; do
    name=${case%%|*}
    rest=${case#*|}
    want=${rest%%|*}
    command=${rest#*|}
    run_hook deny-shell-writes.sh "$(bash_json "$command")"
    expect "deny-shell-writes: $name" "$want"
done

# --- format-python.sh -------------------------------------------------------------------
printf 'x  =  1\n' >"$inproj/fmt.py"
run_hook format-python.sh "$(edit_json "$inproj/fmt.py")"
expect "format-python: formats .py" 0 file_is "$inproj/fmt.py" 'x = 1'

printf 'import os\n' >"$inproj/unused.py"
run_hook format-python.sh "$(edit_json "$inproj/unused.py")"
expect "format-python: keeps unused import, reports F401" 2 file_is "$inproj/unused.py" 'import os'

printf 'def (:\n' >"$inproj/broken.py"
run_hook format-python.sh "$(edit_json "$inproj/broken.py")"
expect "format-python: syntax error" 2

printf 'x  =  1\n' >"$inproj/notes.md"
run_hook format-python.sh "$(edit_json "$inproj/notes.md")"
expect "format-python: ignores .md" 0 file_is "$inproj/notes.md" 'x  =  1'

printf 'x  =  1\n' >"$work/outside.py"
run_hook format-python.sh "$(edit_json "$work/outside.py")"
expect "format-python: ignores file outside project" 0 file_is "$work/outside.py" 'x  =  1'

printf 'x = 1\n' >"$inproj/nouv.py"
run_hook format-python.sh "$(edit_json "$inproj/nouv.py")" PATH=/usr/bin:/bin
expect "format-python: no uv → explicit error" 2 out_has 'uv не найден'

# --- session-context.sh -----------------------------------------------------------------
repo=$work/repo
mkdir -p "$repo"
git -C "$repo" init -q -b master
set +e
out=$(CLAUDE_PROJECT_DIR=$repo "$hooks/session-context.sh" </dev/null 2>&1)
rc=$?
set -e
expect "session-context: repo without origin and commits" 0 out_has '^Branch: master$'
expect "session-context: reports missing origin" 0 out_has '^origin/master: no local ref$'

echo
echo "test-hooks.sh: $passed passed, $failed failed"
((failed == 0))
