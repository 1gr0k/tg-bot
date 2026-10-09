#!/bin/bash
# Проверки проекта: секции lint (ruff format --check, ruff check) и test (pytest).
#
#   scripts/check.sh                      все секции
#   scripts/check.sh --only lint|test     одна секция
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
    echo "usage: scripts/check.sh [--only lint|test] [--test <pytest node id>]" >&2
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
            single=$2
            shift 2
            ;;
        *) usage ;;
    esac
done
case "$only" in "" | lint | test) ;; *) usage ;; esac
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

lint() {
    local log=$1 rc=0
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
test_section() { pytest_run "$1"; }
single_section() { pytest_run "$1" -v "$single"; }

if [[ -n "$single" ]]; then
    section "test-single" "$logs/test-single.log" single_section
else
    [[ "$only" == "" || "$only" == lint ]] && section lint "$logs/lint.log" lint_section
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
