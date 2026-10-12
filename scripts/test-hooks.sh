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
    # cwd — не корень проекта: относительные пути хук обязан разрешать сам, от CLAUDE_PROJECT_DIR.
    out=$(cd "$work" && env CLAUDE_PROJECT_DIR="$root" "$@" "$hooks/$hook" <<<"$json" 2>&1)
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

bash_json() { jq -n --arg c "$1" --arg d "${2:-$root}" '{tool_input: {command: $c}, cwd: $d}'; }
edit_json() { jq -n --arg f "$1" '{tool_input: {file_path: $f}}'; }

# --- deny-shell-writes.sh ---------------------------------------------------------------
# Хук проверяется на временном «проекте» $proj (cwd и CLAUDE_PROJECT_DIR), а не на живом
# репозитории; «вне проекта» — $outside, HOME по умолчанию — $home (тоже вне проекта).
proj=$work/proj
outside=$work/scratchpad
home=$work/home
mkdir -p "$proj/docs" "$outside" "$home"

# shell_case <ожидаемый rc> <имя> <команда> [VAR=значение…] — блок (2) проверяет и текст сообщения.
shell_case() {
    local want=$1 name=$2 cmd=$3
    shift 3
    run_hook deny-shell-writes.sh "$(bash_json "$cmd" "$proj")" CLAUDE_PROJECT_DIR="$proj" HOME="$home" "$@"
    if [[ "$want" == 2 ]]; then
        expect "deny-shell-writes: $name" 2 out_has 'Write/Edit'
    else
        expect "deny-shell-writes: $name" 0
    fi
}

# Перенесено из ~/Developer/TestProj/scripts/test-hooks.sh (хук проверен там на 12 этапах).
shell_case 2 'echo > relative file' 'echo x > a.swift'
shell_case 2 'append to relative file' 'echo x >> docs/a.md'
shell_case 2 'no space before target' 'echo x>a.swift'
shell_case 2 'clobber >|' 'printf x >| notes.txt'
shell_case 2 '&> to file' 'xcodebuild build &> build.log'
shell_case 2 'tee' 'swift test | tee out.txt'
shell_case 2 'tee -a' 'echo x | tee -a log.txt'
shell_case 2 'heredoc into file' $'cat <<EOF > Sources/A.swift\nlet a = 1\nEOF'
shell_case 2 'heredoc via tee' $'tee README.md <<\'EOF\'\n# Title\nEOF'
shell_case 2 'absolute path in project' "echo x > $proj/Garage/A.swift"
shell_case 2 'quoted target in project' 'echo x > "Garage/My File.swift"'
shell_case 2 'variable pointing into project' "DIR=$proj/docs; echo x > \$DIR/a.md"
shell_case 2 'unresolved variable' 'echo x > $UNKNOWN_DIR_FOR_HOOK_TEST/a.md'
shell_case 2 'second command in chain' 'swift build 2>&1 && echo done > status.txt'
shell_case 2 'relative cd inside project' '(cd docs && echo x > a.md)'
shell_case 2 'cd into project' "cd $proj && echo x > a.txt"
shell_case 2 'last assignment wins' "X=/tmp/a; X=$proj/b; echo x > \$X/c"
shell_case 2 '.. that stays in project' 'echo x > Garage/../a.swift'
shell_case 2 'tee after redirect-stripped args' 'swift test 2>/dev/null | tee result.txt'

shell_case 0 '2>&1' 'swift test 2>&1 | tail -20'
shell_case 0 '>&2' 'echo oops >&2'
shell_case 0 '/dev/null' 'git fetch > /dev/null 2>&1'
shell_case 0 '&>/dev/null' 'xcrun simctl list &>/dev/null'
shell_case 0 '/tmp' 'echo x > /tmp/hook-test.txt'
shell_case 0 'scratchpad' "echo x > $outside/out.txt"
shell_case 0 'scratchpad via variable' "TMP=$outside/e2e; openssl rand -hex 16 > \$TMP/secret"
shell_case 0 'tee to scratchpad' "swift test 2>&1 | tee $outside/test.log"
shell_case 0 '> inside quotes' 'echo "a > b" | grep ">"'
shell_case 0 'escaped >' 'echo a \> b'
shell_case 0 'heredoc commit message' $'git commit -m "$(cat <<\'EOF\'\nFix > and >> in docs\nEOF\n)"'
shell_case 0 'heredoc as stdin' $'jq . <<EOF\n{"a": "x > y"}\nEOF'
shell_case 0 'here-string' 'jq . <<< "{}"'
shell_case 0 'no redirect' 'xcodebuild -project Garage.xcodeproj -list | grep -A5 Schemes'
shell_case 0 'committee is not tee' 'echo committee'
shell_case 0 'arithmetic comparison' 'n=3; (( n > 0 )) && echo yes'
shell_case 0 'arithmetic expansion' 'echo $(( 5 > 3 ))'
shell_case 0 '[[ ]] string comparison' '[[ $a > $b ]] && echo later'
shell_case 0 'cd to scratchpad, relative target' "(cd $outside/e2e && openssl rand -hex 16 > secret)"
shell_case 0 'cd to unknown dir' '(cd $SOME_UNSET_DIR_FOR_HOOK_TEST && make > out.txt)'
shell_case 0 'tee as grep argument' 'grep -n "tee" .claude/hooks/deny-shell-writes.sh'
shell_case 0 'tee as rg argument' 'rg tee scripts'
shell_case 0 'tee with stderr redirect' 'swift test | tee /tmp/x.log 2>/dev/null'
shell_case 0 'variable from mktemp' 'out=$(mktemp); swift test > $out'
shell_case 0 'variable from backticks' 'out=`mktemp`; swift test > $out'
shell_case 0 '.. leaving the project' 'echo x > ../outside.txt'

# Новые пары «блок / пропуск» для веток, которых нет среди перенесённых.
# &> и &>>
shell_case 2 '&>> to file' 'make &>> build.log'
shell_case 0 '&>> outside project' 'make &>> /tmp/hook-test.log'
shell_case 0 '&> outside project' 'make &> /tmp/hook-test.log'
# >|
shell_case 0 '>| outside project' 'printf x >| /tmp/hook-test.txt'
# цель после cd <dir>
shell_case 2 'cd inside project without subshell' 'cd docs && echo x > a.md'
shell_case 0 'cd .. leaves the project' 'cd .. && echo x > b.txt'
shell_case 2 'cd to project via variable' "D=$proj/docs; cd \$D && echo x > a.md"
# цель с переменной: разрешённая / запрещённая / неразрешимая
shell_case 2 'braced variable into project' "D=$proj/docs; echo x > \${D}/a.md"
shell_case 0 'braced variable outside project' "D=$outside; echo x > \${D}/a.txt"
shell_case 2 'variable from hook environment (project)' 'echo x > $CLAUDE_PROJECT_DIR/a.txt'
shell_case 0 'variable from hook environment (HOME outside)' 'echo x > $HOME/a.txt'
shell_case 2 'unresolved variable in tee' 'echo x | tee $UNKNOWN_DIR_FOR_HOOK_TEST/a.md'
shell_case 0 'variable from $(…) is not checked' 'out=$(mktemp -d); echo x > $out/a.txt'
# ~
shell_case 2 '~ resolves into project' 'echo x > ~/a.txt' HOME="$proj"
shell_case 0 '~ resolves outside project' 'echo x > ~/a.txt'
shell_case 2 'cd ~ into project' 'cd ~ && echo x > a.txt' HOME="$proj"
shell_case 0 'cd ~ outside project' 'cd ~ && echo x > a.txt'
# ..
shell_case 2 '.. leaves and re-enters the project' 'echo x > ../proj/a.txt'
shell_case 0 '.. from subdirectory leaves the project' 'echo x > docs/../../out.txt'
# [[ a > b ]] и (( a > b )): сравнение пропускается, настоящее перенаправление рядом — нет
shell_case 0 '[[ a > b ]] literal' '[[ a > b ]] && echo later'
shell_case 2 'redirect after [[ a > b ]]' '[[ a > b ]] && echo x > notes.txt'
shell_case 0 '(( a > b )) literal' '(( a > b )) && echo later'
shell_case 2 'redirect after (( a > b ))' '(( a > b )) && echo x > notes.txt'
# tee -a, tee после ; и &&
shell_case 0 'tee -a outside project' 'echo x | tee -a /tmp/hook-test.log'
shell_case 2 'tee after ;' 'echo x; tee notes.txt'
shell_case 0 'tee after ; outside project' 'echo x; tee /tmp/hook-test.log'
shell_case 2 'tee after &&' 'true && tee notes.txt'
shell_case 2 'tee -a after &&' 'true && tee -a notes.txt'
shell_case 0 'tee -a after && outside project' 'true && tee -a /tmp/hook-test.log'
# <<-
shell_case 2 '<<- into file' $'cat <<-EOF > notes.txt\n\thello\n\tEOF'
shell_case 0 '<<- body with > is data' $'jq . <<-EOF\n\t{"a": "x > y"}\n\tEOF'
shell_case 0 'heredoc body with unquoted > is data' $'cat <<EOF\nFix > and >> in docs\nEOF'
shell_case 2 'redirect after <<- body' $'cat <<-EOF\n\thello\n\tEOF\necho y > notes.txt'
shell_case 2 'redirect after << body' $'cat <<EOF\nhello\nEOF\necho y > notes.txt'

# Значение переменной с `&`: в bash 5.2 в замене `${x/…/$v}` голый `&` подставляет совпавший
# текст (patsub_replacement) и ломает путь; значение в кавычках — буквально.
shell_case 0 'variable value with & (outside project)' 'echo x > $AMP_DIR_FOR_HOOK_TEST/a.txt' AMP_DIR_FOR_HOOK_TEST="$outside/a&b"
shell_case 2 'variable value with & (inside project)' 'echo x > $AMP_DIR_FOR_HOOK_TEST/a.txt' AMP_DIR_FOR_HOOK_TEST="$proj/a&b"

# Fail-closed: без jq хук не открывается (exit 2), даже для безобидной команды.
mkdir -p "$work/nobin"
run_hook deny-shell-writes.sh "$(bash_json 'ls' "$proj")" CLAUDE_PROJECT_DIR="$proj" PATH="$work/nobin"
expect "deny-shell-writes: no jq → exit 2" 2 out_has 'jq не найден'
run_hook deny-shell-writes.sh "$(bash_json 'echo x > notes.txt' "$proj")" CLAUDE_PROJECT_DIR="$proj" PATH="$work/nobin"
expect "deny-shell-writes: no jq, redirect → exit 2" 2 out_has 'jq не найден'

# --- deny-hook-bypass.sh ----------------------------------------------------------------
# bypass_case <ожидаемый rc> <имя> <команда> [VAR=значение…] — блок (2) проверяет и текст сообщения.
bypass_case() {
    local want=$1 name=$2 cmd=$3
    shift 3
    run_hook deny-hook-bypass.sh "$(bash_json "$cmd" "$proj")" CLAUDE_PROJECT_DIR="$proj" "$@"
    if [[ "$want" == 2 ]]; then
        expect "deny-hook-bypass: $name" 2 out_has 'Обход git-хуков запрещён'
    else
        expect "deny-hook-bypass: $name" 0
    fi
}

bypass_case 2 '-n first' 'git commit -n -m x'
bypass_case 2 '-n last' 'git commit -m x -n'
bypass_case 2 'cluster -anm' 'git commit -anm x'
bypass_case 2 'cluster -an' 'git commit -an'
bypass_case 2 '--no-verify' 'git commit --no-verify'
bypass_case 2 '--no-verify with message' 'git commit -m x --no-verify'
bypass_case 2 '--no-ve (abbreviation)' 'git commit --no-ve -m x'
bypass_case 2 '-c core.hooksPath' 'git -c core.hooksPath=/dev/null commit -m x'
bypass_case 2 '-c core.hooksPath among other -c' 'git -c user.name=a -c core.hooksPath=/x commit -m x'
bypass_case 2 '-c core.hooksPath, key case' 'git -c CORE.HOOKSPATH=/x commit -m x'
bypass_case 2 '-c core.hooksPath without value' 'git -c core.hooksPath commit -m x'
bypass_case 2 '--config-env core.hooksPath' 'git --config-env=core.hooksPath=H commit -m x'
bypass_case 2 '-C dir before -n' 'git -C /tmp/x commit -n -m x'
bypass_case 2 'after &&' 'git add . && git commit -n -m x'
bypass_case 2 'after ;' 'git status; git commit --no-verify'
bypass_case 2 'in subshell' '(git commit -n -m x)'
bypass_case 2 'in command substitution' 'out=$(git commit -n -m x)'
bypass_case 2 'env assignment before git' 'GIT_TRACE=1 git commit -n -m x'
bypass_case 2 'env wrapper' 'env git commit --no-verify'
bypass_case 2 'git by absolute path' '/usr/bin/git commit -n'
bypass_case 2 'quoted flag' 'git commit "-n" -m x'
bypass_case 2 'line continuation' $'git commit -m x \\\n -n'
bypass_case 2 'flag after message with -n inside' 'git commit -m "has -n" -n'

bypass_case 0 'quoted -n in message' 'git commit -m "handle -n flag"'
bypass_case 0 'quoted --no-verify in message' "git commit -m 'document --no-verify'"
bypass_case 0 '-am' 'git commit -am x'
bypass_case 0 'plain commit' 'git commit -m x'
bypass_case 0 '-m value is -n' 'git commit -m -n'
bypass_case 0 '-mn is message n' 'git commit -mn'
bypass_case 0 '-am value is -n' 'git commit -am -n'
bypass_case 0 '--no-edit is not --no-verify' 'git commit --amend --no-edit'
bypass_case 0 '-S with -v' 'git commit -S -v -m x'
bypass_case 0 '-S<keyid> is attached value' 'git commit -Sn -m x'
bypass_case 0 'path after --' 'git commit -m x -- -n'
bypass_case 0 'git log -n' 'git log -n 5'
bypass_case 0 'git push -n' 'git push -n origin x'
bypass_case 0 'git status -n' 'git status -n'
bypass_case 0 'commit-tree is not commit' 'git commit-tree -n x'
bypass_case 0 'other -c config' 'git -c user.name=a commit -m x'
bypass_case 0 'core.hooksPath in message' 'git commit -m "set core.hooksPath"'
bypass_case 0 '-n in echo' 'echo "git commit -n"'
bypass_case 0 'heredoc message with -n' $'git commit -m "$(cat <<\'EOF\'\nfix -n and --no-verify\nEOF\n)"'
bypass_case 0 'commit with redirect' 'git commit -m x 2>&1 | tail -3'
bypass_case 0 'not git' 'mycommit commit -n'

run_hook deny-hook-bypass.sh "$(bash_json 'git commit -n' "$proj")" CLAUDE_PROJECT_DIR="$proj" PATH="$work/nobin"
expect "deny-hook-bypass: no jq → exit 2" 2 out_has 'jq не найден'

# neutralize_quotes и strip_heredoc_bodies в deny-hook-bypass.sh — копии из deny-shell-writes.sh:
# тела функций должны совпадать.
fn_body() { # <файл> <функция>
    awk -v name="$2" '$0 ~ "^" name "\\(\\) \\{" { p = 1 } p { print } p && /^}/ { exit }' "$1"
}
same_body() { # <функция>
    local a b
    a=$(fn_body "$hooks/deny-shell-writes.sh" "$1")
    b=$(fn_body "$hooks/deny-hook-bypass.sh" "$1")
    [[ -n "$a" && "$a" == "$b" ]]
}
rc=0
out=""
expect "deny-hook-bypass: neutralize_quotes — копия deny-shell-writes.sh" 0 same_body neutralize_quotes
expect "deny-hook-bypass: strip_heredoc_bodies — копия deny-shell-writes.sh" 0 same_body strip_heredoc_bodies

# --- format-python.sh -------------------------------------------------------------------
printf 'x  =  1\n' >"$inproj/fmt.py"
run_hook format-python.sh "$(edit_json "$inproj/fmt.py")"
expect "format-python: formats .py" 0 file_is "$inproj/fmt.py" 'x = 1'

printf 'x  =  1\n' >"$inproj/rel.py"
run_hook format-python.sh "$(edit_json "$(basename "$inproj")/rel.py")"
expect "format-python: relative file_path is formatted" 0 file_is "$inproj/rel.py" 'x = 1'

unused_ok() { file_is "$inproj/unused.py" 'import os' && out_has 'F401'; }
printf 'import os\n' >"$inproj/unused.py"
run_hook format-python.sh "$(edit_json "$inproj/unused.py")"
expect "format-python: keeps unused import, reports F401" 2 unused_ok

broken_ok() { file_is "$inproj/broken.py" 'def (:' && out_has 'ruff format failed'; }
printf 'def (:\n' >"$inproj/broken.py"
run_hook format-python.sh "$(edit_json "$inproj/broken.py")"
expect "format-python: syntax error, file untouched" 2 broken_ok

# Путь начинается в проекте, но `..` выводит наружу: файл не трогается.
printf 'x  =  1\n' >"$work/dotdot.py"
dots=$(sed -e 's|[^/]||g' -e 's|/|/..|g' <<<"$inproj")
run_hook format-python.sh "$(edit_json "$inproj$dots$work/dotdot.py")"
expect "format-python: .. leaving the project" 0 file_is "$work/dotdot.py" 'x  =  1'

# Сам файл — симлинк на файл вне проекта: пропуск, цель не изменена.
printf 'x  =  1\n' >"$work/target.py"
ln -s "$work/target.py" "$inproj/link.py"
run_hook format-python.sh "$(edit_json "$inproj/link.py")"
expect "format-python: symlink file → outside target untouched" 0 file_is "$work/target.py" 'x  =  1'

run_hook format-python.sh "$(edit_json "$inproj/fmt.py")" PATH="$work/nobin"
expect "format-python: no jq → exit 2" 2 out_has 'jq не найден'

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
