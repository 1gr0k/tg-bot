#!/bin/bash
# Автотест git-хука pre-commit (gitleaks) на временных репозиториях.
# Фейковые секреты собираются в рантайме из частей — готовых литералов в репозитории нет.
# Кейсы: каждый секрет блокирует коммит; тот же файл без секрета проходит; PATH без gitleaks — блок.
#
# GIT_HOOKS_SRC / GITLEAKS_CONFIG_SRC — каталог хука и конфиг для проверки на зубы (только копии:
# `GITLEAKS_CONFIG_SRC=<scratchpad>/gitleaks.toml scripts/test-git-hooks.sh`).
# Секреты — без ключевых слов («telegr», «token») в файле: встроенные правила gitleaks их не ловят,
# и кейс проверяет именно свои правила из .gitleaks.toml.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd -P)
hooks_src=$(cd "${GIT_HOOKS_SRC:-$root/scripts/git-hooks}" && pwd -P)
config_src=${GITLEAKS_CONFIG_SRC:-$root/.gitleaks.toml}
echo "git-hooks: $hooks_src, config: $config_src"

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "FAIL test-git-hooks: gitleaks не найден в PATH — проверять нечем"
    exit 1
fi
[[ -f "$config_src" ]] || {
    echo "FAIL test-git-hooks: нет конфига $config_src"
    exit 1
}

# Окружение git не должно протекать ни из хука, ни из пользовательских настроек.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

work=$(mktemp -d "${TMPDIR:-/tmp}/test-git-hooks.XXXXXX")
work=$(cd "$work" && pwd -P)
trap 'rm -rf "$work"' EXIT

passed=0
failed=0
rc=0
out=""

# Фейковые секреты — из частей (в исходнике нет подходящей под правила строки).
# Длины точные, а не «примерно»: границы правила проверяются на байт (см. пп. 18 плана).
# digits_src / tail_src — источники цифр и «хвоста»; нужная длина берётся срезом. В хвосте нет
# `-`/`_` на границах 29/30/35/40/41 символов (индексы 28, 29, 34, 39, 40 — буквы или цифры),
# хвост не начинается с `AA`.
digits_src=12345678901234567890
tail_src=abcdEFGH_ijkl-MNOPqrst0123456789xyzQWERTYuiopASDFGHJ
cookie_val=0123456789abcdefABCDEF%3Awxyz
cookie_decoded=0123456789abcdefABCDEF:wxyz # gitleaks проверяет и percent-decoded вариант
tab=$(printf '\t')

digits() { printf '%s' "${digits_src:0:$1}"; }
tail_of() { printf '%s' "${tail_src:0:$1}"; }

# Современный формат: ID 10 цифр, `AA` + 33 символа (вторая часть — 35). Старый: ID 6 цифр,
# вторая часть 35 символов, не с `AA`.
token_modern="$(digits 10):AA$(tail_of 33)"
token_old="$(digits 6):$(tail_of 35)"
netscape=".instagram.com${tab}TRUE${tab}/${tab}TRUE${tab}1893456000${tab}sessionid${tab}${cookie_val}"
pair="sessionid=${cookie_val}"

# commit <repo> [PATH] — коммит с хуком; результат в $rc и $out.
commit() {
    local repo=$1 path=${2:-$PATH}
    set +e
    out=$(cd "$repo" && PATH=$path git commit -q -m test 2>&1)
    rc=$?
    set -e
}

expect_ok() { # <имя> — прошлый commit должен пройти
    if ((rc == 0)); then
        echo "ok   $1"
        passed=$((passed + 1))
    else
        echo "FAIL $1: exit $rc, expected 0"
        sed 's/^/     | /' <<<"$out"
        failed=$((failed + 1))
    fi
}

expect_block() { # <имя> <регэксп вывода>… — прошлый commit отклонён, и в выводе есть каждый регэксп
    local name=$1 pattern
    shift
    if ((rc == 0)); then
        echo "FAIL $name: commit прошёл, expected block"
        failed=$((failed + 1))
        return
    fi
    for pattern in "$@"; do
        if ! grep -qE -- "$pattern" <<<"$out"; then
            echo "FAIL $name: отклонён (exit $rc), но без ожидаемого вывода /$pattern/"
            sed 's/^/     | /' <<<"$out"
            failed=$((failed + 1))
            return
        fi
    done
    echo "ok   $name"
    passed=$((passed + 1))
}

# new_repo — временный репозиторий с хуком и конфигом из проверяемых источников; печатает путь.
new_repo() {
    local repo
    repo=$(mktemp -d "$work/repo.XXXXXX")
    mkdir -p "$repo/scripts"
    git -C "$repo" init -q -b master
    git -C "$repo" config user.name test
    git -C "$repo" config user.email test@example.invalid
    cp -R "$hooks_src" "$repo/scripts/git-hooks"
    cp "$config_src" "$repo/.gitleaks.toml"
    git -C "$repo" config core.hooksPath scripts/git-hooks
    printf 'init\n' >"$repo/README"
    git -C "$repo" add README
    echo "$repo"
}

# expect_hidden <имя> <значение>… — ни одно значение не встречается в выводе прошлого commit.
# Хук печатает находку целиком (`-v`), секрет в ней скрывает только `--redact`.
expect_hidden() {
    local name=$1 value
    shift
    for value in "$@"; do
        if grep -qF -- "$value" <<<"$out"; then
            echo "FAIL $name: значение секрета есть в выводе хука"
            failed=$((failed + 1))
            return
        fi
    done
    echo "ok   $name"
    passed=$((passed + 1))
}

# secret_case <имя> <строка с секретом> <RuleID> <значение секрета>… — в файле секрет блокирует
# (в выводе — найдена хотя бы одна утечка, именно это правило — не сработало другое, например
# встроенное — и пометка REDACTED, а самого значения нет), тот же файл без него проходит.
secret_case() {
    local name=$1 secret=$2 rule=$3 repo
    shift 3
    repo=$(new_repo)
    commit "$repo"
    expect_ok "$name: репозиторий без секрета (контроль)"
    printf 'first line\n%s\nlast line\n' "$secret" >"$repo/notes.txt"
    git -C "$repo" add notes.txt
    commit "$repo"
    expect_block "$name: коммит с секретом отклонён" 'leaks found: [1-9]' "RuleID:[[:space:]]+$rule\$" REDACTED
    expect_hidden "$name: значение секрета не попало в вывод" "$@"
    printf 'first line\n%s\nlast line\n' "placeholder" >"$repo/notes.txt"
    git -C "$repo" add notes.txt
    commit "$repo"
    expect_ok "$name: тот же файл без секрета проходит"
}

# pass_case <имя> <содержимое> — файл с таким содержимым коммитится (не секрет по правилам).
pass_case() {
    local name=$1 content=$2 repo
    repo=$(new_repo)
    printf 'first line\n%s\nlast line\n' "$content" >"$repo/notes.txt"
    git -C "$repo" add notes.txt
    commit "$repo"
    expect_ok "$name: проходит"
}

token_rule=telegram-bot-token-wide
secret_case "токен, современный формат" "$token_modern" "$token_rule" "$token_modern"
secret_case "токен, старый формат" "$token_old" "$token_rule" "$token_old"
secret_case "токен после = (X=<токен>)" "x=$token_modern" "$token_rule" "$token_modern"
secret_case "токен в URL Bot API (…/bot<ID>:<…>/getMe)" "https://api.telegram.org/bot${token_modern}/getMe" "$token_rule" "$token_modern"
secret_case "cookies Netscape (sessionid)" "$netscape" instagram-cookies-netscape "$cookie_val" "$cookie_decoded"
secret_case "пара sessionid и значение через равно" "$pair" instagram-sessionid-pair "$cookie_val" "$cookie_decoded"

# Границы длины правила токена: ID — 5…12 цифр, вторая часть — 30…40 символов.
# Внутри границ — блок, на 1 короче или длиннее — проходит (не токен: ожидание записано в плане, п. 18).
boundary_case() { # <имя> <цифр в ID> <символов в хвосте>
    local token
    token="$(digits "$2"):$(tail_of "$3")"
    secret_case "$1" "$token" "$token_rule" "$token"
}
boundary_case "токен: ID 5 цифр (нижняя граница)" 5 35
boundary_case "токен: ID 12 цифр (верхняя граница)" 12 35
boundary_case "токен: хвост 30 символов (нижняя граница)" 10 30
boundary_case "токен: хвост 40 символов (верхняя граница)" 10 40
pass_case "токен: ID 4 цифры (на 1 короче)" "$(digits 4):$(tail_of 35)"
pass_case "токен: ID 13 цифр (на 1 длиннее)" "$(digits 13):$(tail_of 35)"
pass_case "токен: хвост 29 символов (на 1 короче)" "$(digits 10):$(tail_of 29)"
pass_case "токен: хвост 41 символ (на 1 длиннее)" "$(digits 10):$(tail_of 41)"

# PATH без gitleaks — блок, даже когда в коммите нет секретов. PATH — временный каталог с
# симлинком только на git (хук до проверки gitleaks вызывает лишь builtin `command -v`), а не
# /usr/bin: там на другой машине может оказаться свой gitleaks, и кейс молча перестанет проверять.
safe_path=$work/bin-git-only
mkdir -p "$safe_path"
ln -s "$(command -v git)" "$safe_path/git"
if PATH=$safe_path command -v gitleaks >/dev/null 2>&1; then
    echo "FAIL fail-closed: gitleaks найден в $safe_path — кейс не проверяет отсутствие"
    failed=$((failed + 1))
elif ! PATH=$safe_path git --version >/dev/null 2>&1; then
    echo "FAIL fail-closed: git из $safe_path не запускается — кейс не проверяет хук"
    failed=$((failed + 1))
else
    repo=$(new_repo)
    printf 'clean\n' >"$repo/clean.txt"
    git -C "$repo" add clean.txt
    commit "$repo" "$safe_path"
    expect_block "fail-closed: PATH без gitleaks — блок" 'gitleaks не найден'
    commit "$repo"
    expect_ok "fail-closed: тот же коммит с gitleaks в PATH проходит (контроль)"
fi

echo
echo "test-git-hooks.sh: $passed passed, $failed failed"
((failed == 0))
