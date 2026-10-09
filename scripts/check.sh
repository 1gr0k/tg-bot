#!/bin/bash
# Проверки проекта: секции lint (ruff format --check, ruff check), hooks (scripts/test-hooks.sh)
# и test (pytest).
#
#   scripts/check.sh                          все секции
#   scripts/check.sh --only lint|hooks|test   одна секция
#   scripts/check.sh --test <node id>     один тест pytest (например, tests/test_x.py::test_y)
#
# Полный вывод — в .build/check-logs/<секция>.log (для --test — test-single.log); при провале
# на экран выводится хвост лога. Последняя строка — итог: `check.sh: all checks passed` или
# `check.sh: section "<имя>" FAILED` (по строке на каждую упавшую секцию). Код возврата 0 — успех.
#
# pytest с кодом 5 («тесты не найдены») — провал: проверка, которая ничего не запустила,
# не может считаться зелёной.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$root"
logs=.build/check-logs

usage() {
    echo "usage: scripts/check.sh [--only lint|hooks|test] [--test <pytest node id>]" >&2
    exit 64
}

only=""
single=""
while (($# > 0)); do
    case "$1" in
        --only)
            (($# >= 2)) || usage
            only=$2
            shift 2
            ;;
        --test)
            (($# >= 2)) || usage
            [[ -n "$2" && "$2" != -* ]] || usage
            single=$2
            shift 2
            ;;
        *) usage ;;
    esac
done
case "$only" in "" | lint | hooks | test) ;; *) usage ;; esac
[[ -z "$only" || -z "$single" ]] || usage

if ! command -v uv >/dev/null 2>&1; then
    echo "check.sh: uv не найден в PATH"
    echo 'check.sh: section "setup" FAILED'
    exit 1
fi
mkdir -p "$logs"

# run <log> <команда…> — дописывает команду и её вывод в лог; возвращает код команды.
run() {
    local log=$1 rc
    shift
    echo "\$ $*" >>"$log"
    set +e
    "$@" >>"$log" 2>&1
    rc=$?
    set -e
    echo "exit=$rc" >>"$log"
    return "$rc"
}

# lint_scope <log> — множество файлов, которые видит ruff (`--show-files`), должно совпадать с
# .py/.pyi в git (отслеживаемые и неотслеживаемые не-ignored). Иначе правка `include`/`exclude`
# даёт зелёный lint вхолостую. Нет файлов ни там, ни там — зелёный с явной строкой.
lint_scope() {
    local log=$1 rc=0 tmp ruff_out diff_out f
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/check-scope.XXXXXX")
    echo '$ сравнение ruff check --show-files с git ls-files' >>"$log"
    set +e
    uv run --frozen ruff check --show-files . >"$tmp/ruff.raw" 2>>"$log"
    rc=$?
    set -e
    if ((rc != 0)); then
        echo "check.sh: lint: ruff --show-files завершился с кодом $rc" | tee -a "$log"
        rm -rf "$tmp"
        return 1
    fi
    sed -e "s|^$root/||" -e 's|^\./||' "$tmp/ruff.raw" | LC_ALL=C sort -u >"$tmp/ruff.txt"
    : >"$tmp/git.raw"
    git ls-files --cached --others --exclude-standard -- '*.py' '*.pyi' >"$tmp/git.all"
    while IFS= read -r f; do
        [[ ! -f "$f" ]] || echo "$f" >>"$tmp/git.raw"
    done <"$tmp/git.all"
    LC_ALL=C sort -u "$tmp/git.raw" >"$tmp/git.txt"
    if [[ ! -s "$tmp/ruff.txt" && ! -s "$tmp/git.txt" ]]; then
        echo "lint: .py файлов нет — проверять нечего" | tee -a "$log"
    else
        diff_out=$(LC_ALL=C comm -23 "$tmp/git.txt" "$tmp/ruff.txt" | sed 's/^/    в git, ruff не видит: /')
        ruff_out=$(LC_ALL=C comm -13 "$tmp/git.txt" "$tmp/ruff.txt" | sed 's/^/    ruff видит, в git нет: /')
        if [[ -n "$diff_out$ruff_out" ]]; then
            {
                echo "check.sh: lint: набор файлов ruff расходится с git:"
                [[ -z "$diff_out" ]] || echo "$diff_out"
                [[ -z "$ruff_out" ]] || echo "$ruff_out"
            } | tee -a "$log"
            rc=1
        else
            rc=0
        fi
    fi
    rm -rf "$tmp"
    return "$rc"
}

lint() {
    local log=$1 rc=0
    lint_scope "$log" || rc=1
    run "$log" uv run --frozen ruff format --check . || rc=1
    run "$log" uv run --frozen ruff check . || rc=1
    return "$rc"
}

# pytest_run <log> <аргументы pytest…>
pytest_run() {
    local log=$1 rc=0
    shift
    run "$log" uv run --frozen pytest "$@" || rc=$?
    if ((rc == 5)); then
        echo "check.sh: pytest: тесты не найдены (exit 5)" | tee -a "$log"
    fi
    return "$rc"
}

failed=()

# section <имя> <лог> <функция> [аргументы…]
section() {
    local name=$1 log=$2
    shift 2
    : >"$log"
    echo "check.sh: section \"$name\"…"
    if "$@" "$log"; then
        echo "check.sh: section \"$name\" passed (log: $log)"
    else
        echo "--- tail of $log"
        tail -n 30 "$log"
        echo "--- full log: $log"
        failed+=("$name")
    fi
}

lint_section() { lint "$1"; }
hooks_section() { run "$1" scripts/test-hooks.sh; }
test_section() { pytest_run "$1"; }
single_section() { pytest_run "$1" -v "$single"; }

if [[ -n "$single" ]]; then
    section "test-single" "$logs/test-single.log" single_section
else
    [[ "$only" == "" || "$only" == lint ]] && section lint "$logs/lint.log" lint_section
    [[ "$only" == "" || "$only" == hooks ]] && section hooks "$logs/hooks.log" hooks_section
    [[ "$only" == "" || "$only" == test ]] && section test "$logs/test.log" test_section
fi

if ((${#failed[@]} == 0)); then
    echo "check.sh: all checks passed"
    exit 0
fi
for name in "${failed[@]}"; do
    echo "check.sh: section \"$name\" FAILED"
done
exit 1
