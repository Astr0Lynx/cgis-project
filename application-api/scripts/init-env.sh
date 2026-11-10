#!/bin/sh
# Append the application API settings (application-api/.env.example) to the root .env, replacing every
# CHANGE_ME with a random value. Keys already present in .env are left untouched; nothing is overwritten.
# Run after infra/scripts/init-env.sh. POSIX sh; works in Git Bash on Windows.
set -eu
cd "$(dirname "$0")/../.."
if [ ! -e .env ]; then
    echo "app init-env: .env does not exist; run: sh infra/scripts/init-env.sh" >&2
    exit 1
fi
rand() { od -An -N18 -tx1 /dev/urandom | tr -d ' \n'; }
umask 077
added=0
tmp=".env.app.tmp"
: > "$tmp"
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    key="${line%%=*}"
    if grep -q "^${key}=" .env; then
        continue
    fi
    case "$line" in
        *=CHANGE_ME) printf '%s%s\n' "${line%CHANGE_ME}" "$(rand)" >> "$tmp" ;;
        *) printf '%s\n' "$line" >> "$tmp" ;;
    esac
    added=$((added + 1))
done < application-api/.env.example
if [ "$added" -gt 0 ]; then
    {
        printf '\n# --- CyberGIS application API (added by application-api/scripts/init-env.sh)\n'
        cat "$tmp"
    } >> .env
fi
rm -f "$tmp"
echo "app init-env: added $added application API setting(s) to .env (existing keys unchanged)"
