#!/bin/bash
# Установка gitleaks в CI (ubuntu, linux x64) для job'ов `hooks` и `gitleaks`: скачивает релизный
# архив и сверяет его sha256 с значением, зашитым в workflow (`GITLEAKS_SHA256` в env), — а не с
# checksums.txt из того же релиза: подменённый релиз подменил бы и файл сумм.
# При смене GITLEAKS_VERSION значение берётся из gitleaks_<версия>_checksums.txt релиза (строка
# для gitleaks_<версия>_linux_x64.tar.gz) и обновляется вместе с версией.
#
# Env: GITLEAKS_VERSION, GITLEAKS_SHA256 (64 hex, нижний регистр) — обязательны.
# Для проверки без сети: GITLEAKS_BASE_URL (по умолчанию релизы GitHub; подходит file://…),
# INSTALL_DIR (по умолчанию /usr/local/bin; нет прав записи — через sudo).
set -euo pipefail

: "${GITLEAKS_VERSION:?install-gitleaks: GITLEAKS_VERSION не задан}"
: "${GITLEAKS_SHA256:?install-gitleaks: GITLEAKS_SHA256 не задан}"
if [[ ! "$GITLEAKS_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "install-gitleaks: GITLEAKS_SHA256 — не 64 символа hex в нижнем регистре" >&2
    exit 1
fi
base=${GITLEAKS_BASE_URL:-https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}}
install_dir=${INSTALL_DIR:-/usr/local/bin}
archive="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/install-gitleaks.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/$archive" "$base/$archive"
actual=$(sha256_of "$tmp/$archive")
if [[ "$actual" != "$GITLEAKS_SHA256" ]]; then
    echo "install-gitleaks: sha256 архива $archive не совпал с GITLEAKS_SHA256" >&2
    echo "install-gitleaks:   ожидалось: $GITLEAKS_SHA256" >&2
    echo "install-gitleaks:   получено:  $actual" >&2
    exit 1
fi

tar -xzf "$tmp/$archive" -C "$tmp" gitleaks
if [[ -w "$install_dir" ]]; then
    install -m 0755 "$tmp/gitleaks" "$install_dir/gitleaks"
else
    sudo install -m 0755 "$tmp/gitleaks" "$install_dir/gitleaks"
fi
"$install_dir/gitleaks" version
