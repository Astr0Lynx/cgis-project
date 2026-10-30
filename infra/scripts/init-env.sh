#!/bin/sh
# Create .env from .env.example, replacing every CHANGE_ME with a random 32-character hex value.
# Refuses to overwrite an existing .env. POSIX sh; works in Git Bash on Windows.
set -eu
cd "$(dirname "$0")/../.."
if [ -e .env ]; then
    echo "init-env: .env already exists; leaving it unchanged" >&2
    exit 0
fi
rand() { od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }
umask 077
: > .env.tmp
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        *=CHANGE_ME) printf '%s%s\n' "${line%CHANGE_ME}" "$(rand)" >> .env.tmp ;;
        *) printf '%s\n' "$line" >> .env.tmp ;;
    esac
done < .env.example
mv .env.tmp .env
echo "init-env: wrote .env with generated local passwords"
