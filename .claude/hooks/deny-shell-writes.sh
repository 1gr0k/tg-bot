#!/bin/bash
# PreToolUse (Bash): отклоняет запись в файлы проекта через shell — `>`, `>>`, `>|`, `&>`,
# `tee` и heredoc с перенаправлением в файл. Файлы создаются и правятся только Write/Edit
# (на них работают хуки, /rewind и права доступа).
#
# Это защита от привычки (перенаправления), а не от намеренного обхода: `cp`, `mv`,
# `sed -i`, `python -c`, `dd` и т.п. не ловятся. Разбор приблизительный и при сомнении
# скорее пропускает, чем блокирует законную команду: ложный запрет мешает работе сильнее,
# чем пропущенная привычка.
#
# Разрешено: `2>&1` / `>&2`, `/dev/null` и `/dev/std*`, любые пути вне проекта
# (scratchpad, /tmp, $TMPDIR). Относительная цель считается от `cd <dir>` в той же команде,
# иначе от cwd. Цель с переменной (`> $TMP/x`) вычисляется по последнему присваиванию в
# команде или по окружению; значение из `$(…)` (например, mktemp) не проверяется; не
# вычислилась — запрет. `>` внутри `((…))` и `[[…]]` — сравнение, а не перенаправление.
# Известные пропуски: второй heredoc в одной строке, `<<` внутри кавычек.
# Exit 2 возвращает сообщение Claude; без jq хук тоже закрывается (exit 2). Кейсы — scripts/test-hooks.sh.
set -euo pipefail
set -f # аргументы tee разбиваются на слова без раскрытия масок

# Fail-closed: PreToolUse открывает любой код, кроме 2, — без jq хук молча пропускал бы всё.
if ! command -v jq >/dev/null 2>&1; then
    echo "deny-shell-writes: jq не найден в PATH — команда не проверена и заблокирована." >&2
    exit 2
fi

input=$(cat)
command=$(jq -r '.tool_input.command // empty' <<<"$input")
[[ -n "$command" ]] || exit 0
cwd=$(jq -r '.cwd // empty' <<<"$input")
cwd=${cwd:-$PWD}
project=${CLAUDE_PROJECT_DIR:-$cwd}
project=${project%/}

# Тела heredoc — данные, а не команды: выбрасываем их (строку с `<<` оставляем).
strip_heredoc_bodies() {
    awk '
        pending != "" {
            line = $0
            if (strip_tabs) sub(/^\t+/, "", line)
            if (line == pending) pending = ""
            next
        }
        {
            print
            probe = $0
            gsub(/<<</, "", probe)
            if (match(probe, /<<-?[ \t]*[\047"]?[A-Za-z_][A-Za-z0-9_]*/)) {
                spec = substr(probe, RSTART, RLENGTH)
                strip_tabs = (substr(spec, 3, 1) == "-")
                gsub(/^<<-?[ \t]*[\047"]?/, "", spec)
                pending = spec
            }
        }
    '
}

# Кавычки снимаются, а спецсимволы внутри них заменяются на `_`, чтобы `echo "a > b"`
# не выглядел перенаправлением, а `> "/tmp/x"` остался целью `/tmp/x`.
neutralize_quotes() {
    local s=$1 out="" quote="" c i
    for ((i = 0; i < ${#s}; i++)); do
        c=${s:i:1}
        if [[ -n "$quote" ]]; then
            if [[ "$c" == "$quote" ]]; then
                quote=""
                continue
            fi
            if [[ "$quote" == '"' && "$c" == '\' ]]; then
                i=$((i + 1))
                out+="_"
                continue
            fi
            case "$c" in [\<\>\|\;\&\(\)[:space:]]) c="_" ;; esac
            out+=$c
        else
            case "$c" in
                \' | \")
                    quote=$c
                    continue
                    ;;
                \\)
                    i=$((i + 1))
                    out+="_"
                    continue
                    ;;
            esac
            out+=$c
        fi
    done
    printf '%s' "$out"
}

text=$(neutralize_quotes "$(strip_heredoc_bodies <<<"$command")")
# Арифметика и `[[ ]]`: `>` там — сравнение.
text=$(sed -E 's/\(\([^()]*\)\)//g; s/\[\[([^]]|\][^]])*\]\]//g' <<<"$text")

redirect_pattern='(&>>?|[0-9]*>[>|]?)[[:space:]]*&?[^[:space:];&|<>()]*'
substitution_marker='<command substitution>'

# Подставляет $NAME / ${NAME}: последнее присваивание NAME=... в команде, иначе окружение хука.
# Значение из `$(…)`/`…` — маркер подстановки; не вычислилось — пустая строка.
expand_vars() {
    local target=$1 name value assignment rounds=0
    while [[ "$target" =~ \$\{?([A-Za-z_][A-Za-z0-9_]*)\}? ]]; do
        rounds=$((rounds + 1))
        ((rounds <= 5)) || return 0
        name=${BASH_REMATCH[1]}
        assignment=$(grep -oE "(^|[[:space:];&|(])$name=[^[:space:];&|()]*" <<<"$text" | tail -n1 || true)
        if [[ -n "$assignment" ]]; then
            value=${assignment#*"$name="}
            if [[ "$value" == '$' || "$value" == '$('* || "$value" == '`'* ]]; then
                printf '%s' "$substitution_marker"
                return 0
            fi
        elif [[ -n "${!name:-}" ]]; then
            value=${!name}
        else
            return 0
        fi
        target=${target/"${BASH_REMATCH[0]}"/$value}
    done
    [[ "$target" != *'$'* ]] || return 0
    printf '%s' "$target"
}

# Убирает `.` и `..` без обращения к файловой системе.
normalize_path() {
    local IFS=/ part result=()
    for part in $1; do
        case "$part" in
            "" | .) ;;
            ..) [[ ${#result[@]} -eq 0 ]] || unset 'result[${#result[@]}-1]' ;;
            *) result+=("$part") ;;
        esac
    done
    # Без аргументов printf всё равно выводит формат один раз — получается `/`.
    printf '/%s' "${result[@]+"${result[@]}"}"
}

# Каталог последнего `cd <dir>` в команде (для относительных целей), иначе cwd.
base_dir=$cwd
cd_arg=$(grep -oE '(^|[;&|(])[[:space:]]*cd[[:space:]]+[^[:space:];&|<>()]+' <<<"$text" | tail -n1 \
    | sed -E 's/.*cd[[:space:]]+//' || true)
if [[ -n "$cd_arg" ]]; then
    cd_dir=$(expand_vars "$cd_arg")
    [[ "$cd_dir" != "~"* ]] || cd_dir=$HOME${cd_dir#\~}
    case "$cd_dir" in
        "" | "$substitution_marker" | -*) base_dir="" ;; # не вычислить — относительные цели не проверяем
        /*) base_dir=$cd_dir ;;
        *) base_dir=$cwd/$cd_dir ;;
    esac
fi

deny() {
    echo "Запись в файлы проекта через shell запрещена: используй Write/Edit ($1)." >&2
    exit 2
}

check_target() {
    local raw=$1 path
    [[ -n "$raw" && "$raw" != "&"* ]] || return 0
    path=$(expand_vars "$raw")
    [[ -n "$path" ]] || deny "не удалось вычислить путь: $raw"
    [[ "$path" != "$substitution_marker" ]] || return 0
    [[ "$path" != "~"* ]] || path=$HOME${path#\~}
    if [[ "$path" != /* ]]; then
        [[ -n "$base_dir" ]] || return 0
        path=$base_dir/$path
    fi
    case "$path" in
        /dev/null | /dev/stdout | /dev/stderr | /dev/fd/*) return 0 ;;
    esac
    path=$(normalize_path "$path")
    case "$path" in
        "$project" | "$project"/*) deny "$raw" ;;
    esac
}

while IFS= read -r redirect; do
    [[ -n "$redirect" ]] || continue
    target=$(sed -E 's/^(&>>?|[0-9]*>[>|]?)[[:space:]]*//' <<<"$redirect")
    check_target "$target"
done < <(grep -oE "$redirect_pattern" <<<"$text" || true)

# tee — только в позиции команды; перенаправления (`2>/dev/null`) из его аргументов убраны.
tee_text=$(sed -E "s/$redirect_pattern//g" <<<"$text")
while IFS= read -r invocation; do
    [[ -n "$invocation" ]] || continue
    for word in ${invocation#*tee}; do
        [[ "$word" == -* ]] || check_target "$word"
    done
done < <(grep -oE '(^|[;&|(])[[:space:]]*tee([[:space:]]+[^[:space:];&|<>()]+)*' <<<"$tee_text" || true)

exit 0
