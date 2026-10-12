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
digits_a=1234567890
digits_b=123456
chars33=abcdEFGH_ijkl-MNOPqrst0123456789xy
chars35=abcdEFGH_ijkl-MNOPqrst0123456789xyzQW
cookie_val=0123456789abcdefABCDEF%3Awxyz
tab=$(printf '\t')

token_modern="$digits_a:AA${chars33}"
token_old="$digits_b:${chars35}"
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

expect_block() { # <имя> <регэксп вывода> — прошлый commit должен быть отклонён
    if ((rc == 0)); then
        echo "FAIL $1: commit прошёл, expected block"
        failed=$((failed + 1))
    elif ! grep -qE -- "$2" <<<"$out"; then
        echo "FAIL $1: отклонён (exit $rc), но без ожидаемого вывода /$2/"
        sed 's/^/     | /' <<<"$out"
        failed=$((failed + 1))
    else
        echo "ok   $1"
        passed=$((passed + 1))
    fi
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

# secret_case <имя> <строка с секретом> — в файле секрет блокирует, тот же файл без него проходит.
secret_case() {
    local name=$1 secret=$2 repo
    repo=$(new_repo)
    commit "$repo"
    expect_ok "$name: репозиторий без секрета (контроль)"
    printf 'first line\n%s\nlast line\n' "$secret" >"$repo/notes.txt"
    git -C "$repo" add notes.txt
    commit "$repo"
    expect_block "$name: коммит с секретом отклонён" 'leaks found'
    printf 'first line\n%s\nlast line\n' "placeholder" >"$repo/notes.txt"
    git -C "$repo" add notes.txt
    commit "$repo"
    expect_ok "$name: тот же файл без секрета проходит"
}

secret_case "токен, современный формат" "$token_modern"
secret_case "токен, старый формат" "$token_old"
secret_case "cookies Netscape (sessionid)" "$netscape"
secret_case "пара sessionid и значение через равно" "$pair"

# PATH без gitleaks — блок, даже когда в коммите нет секретов.
safe_path=/usr/bin:/bin
if PATH=$safe_path command -v gitleaks >/dev/null 2>&1; then
    echo "FAIL fail-closed: gitleaks найден в $safe_path — кейс не проверяет отсутствие"
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
