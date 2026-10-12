#!/bin/bash
# PreToolUse (Bash): отклоняет `git commit`, который обходит git-хуки (pre-commit с gitleaks):
#   - `--no-verify` (и сокращения до `--no-ve`), короткие кластеры флагов с `n`: `-n`, `-an`, `-anm`;
#   - глобальная настройка `-c core.hooksPath=…` (и `--config-env=core.hooksPath=…`) перед `commit`,
#     в том числе `git -c … commit` и `git -C <dir> -c … commit`.
# Заменяет deny-шаблоны `Bash(git commit*--no-verify*)` и `Bash(git commit*-n *)` из settings.json:
# шаблон по подстроке блокировал `git commit -m "handle -n flag"` и не видел `-anm`/`-c core.hooksPath`.
#
# Это защита от привычки, а не от намеренного обхода: `git config core.hooksPath`, переменные
# окружения git, `GIT_DIR`, правка `.git/hooks`, алиасы git и т.п. не ловятся. Гарантия, что секрет
# не попадёт в историю, — job `gitleaks` в CI по всей истории (.github/workflows/ci.yml).
# При сомнении разбор скорее пропускает, чем блокирует законную команду.
#
# Кавычки снимаются как в deny-shell-writes.sh: текст внутри кавычек (сообщение коммита) не
# разбирается, `-m "handle -n flag"` — одно слово. `neutralize_quotes` и `strip_heredoc_bodies` —
# КОПИИ из deny-shell-writes.sh; править синхронно (scripts/test-hooks.sh сверяет тела функций).
# Exit 2 возвращает сообщение Claude; без jq хук тоже закрывается (exit 2).
# Кейсы — scripts/test-hooks.sh.
set -euo pipefail
set -f # слова команды разбираются без раскрытия масок

# Fail-closed: PreToolUse открывает любой код, кроме 2, — без jq хук молча пропускал бы всё.
if ! command -v jq >/dev/null 2>&1; then
    echo "deny-hook-bypass: jq не найден в PATH — команда не проверена и заблокирована." >&2
    exit 2
fi

input=$(cat)
command=$(jq -r '.tool_input.command // empty' <<<"$input")
[[ -n "$command" ]] || exit 0

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

deny() {
    echo "Обход git-хуков запрещён ($1): pre-commit проверяет индекс на секреты (gitleaks). Коммить без обхода." >&2
    exit 2
}

# Ключ `-c`/`--config-env` — core.hooksPath (регистр ключей git не важен), с `=значение` или без.
check_config_key() {
    local key=${1%%=*}
    key=$(tr '[:upper:]' '[:lower:]' <<<"$key")
    [[ "$key" != "core.hookspath" ]] || deny "git -c core.hooksPath"
}

# Короткие опции `git commit` с обязательным аргументом: остаток кластера — значение, а не флаги
# (`-mn` — сообщение «n», не `-n`); нет остатка — значение в следующем слове (`-m -n`).
needs_value_letters='mFCct'
# Опции с необязательным аргументом, который пишется слитно (`-S<keyid>`, `-u<mode>`): остаток
# кластера — значение, следующее слово не занимают.
attached_value_letters='Su'

# check_commit_args <слово…> — аргументы после `commit`.
check_commit_args() {
    local arg letters letter i skip_next=0
    for arg in "$@"; do
        if ((skip_next)); then
            skip_next=0
            continue
        fi
        case "$arg" in
            --) return 0 ;;
            # Любой префикс `--no-verify` от `--no-ve` (git принимает однозначные сокращения).
            --no-ve*) [[ "--no-verify" != "$arg"* ]] || deny "$arg (--no-verify)" ;;
            --*) ;;
            -?*)
                letters=${arg#-}
                for ((i = 0; i < ${#letters}; i++)); do
                    letter=${letters:i:1}
                    [[ "$letter" != n ]] || deny "$arg (флаг -n = --no-verify)"
                    if [[ "$needs_value_letters" == *"$letter"* ]]; then
                        ((i + 1 < ${#letters})) || skip_next=1
                        break
                    fi
                    [[ "$attached_value_letters" != *"$letter"* ]] || break
                done
                ;;
        esac
    done
}

# check_segment <слово…> — одна простая команда: [VAR=x…] [обёртка…] git [глобальные опции] commit …
check_segment() {
    local -a w=("$@")
    local n=${#w[@]} i=0 word
    # Присваивания и обёртки перед командой.
    while ((i < n)); do
        word=${w[i]}
        case "$word" in
            [A-Za-z_]*=*) i=$((i + 1)) ;;
            env | command | exec | nohup | time | sudo | builtin)
                i=$((i + 1))
                while ((i < n)) && [[ "${w[i]}" == -* ]]; do i=$((i + 1)); done
                ;;
            *) break ;;
        esac
    done
    ((i < n)) || return 0
    [[ "${w[i]##*/}" == git ]] || return 0
    i=$((i + 1))
    # Глобальные опции git до подкоманды.
    while ((i < n)); do
        word=${w[i]}
        case "$word" in
            -c)
                ((i + 1 < n)) && check_config_key "${w[i + 1]}"
                i=$((i + 2))
                ;;
            -c?*)
                check_config_key "${word#-c}"
                i=$((i + 1))
                ;;
            --config-env)
                ((i + 1 < n)) && check_config_key "${w[i + 1]}"
                i=$((i + 2))
                ;;
            --config-env=*)
                check_config_key "${word#--config-env=}"
                i=$((i + 1))
                ;;
            -C | --git-dir | --work-tree | --namespace | --super-prefix) i=$((i + 2)) ;;
            -*) i=$((i + 1)) ;;
            *) break ;;
        esac
    done
    ((i < n)) || return 0
    [[ "${w[i]}" == commit ]] || return 0
    i=$((i + 1))
    if ((i < n)); then
        check_commit_args "${w[@]:i}"
    fi
}

text=$(neutralize_quotes "$(strip_heredoc_bodies <<<"$command")")
# Разделители простых команд — `;`, `&`, `|`, скобки, обратные кавычки, перевод строки.
segments=$(tr ';&|()`' '\n\n\n\n\n\n' <<<"$text")
while IFS= read -r segment; do
    [[ -n "$segment" ]] || continue
    read -r -a words <<<"$segment" || true
    ((${#words[@]} > 0)) || continue
    check_segment "${words[@]}"
done <<<"$segments"

exit 0
