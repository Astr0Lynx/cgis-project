#!/bin/sh
# Create .env from .env.example, replacing every CHANGE_ME with a random 32-character hex value.
# If .env already exists, only APPEND the keys of .env.example that it lacks (new settings added by an
# upgrade), with fresh random secrets; existing lines are never changed. POSIX sh; works in Git Bash.
set -eu
cd "$(dirname "$0")/../.."
# Print 32 random hex characters. Used by render.
rand() { od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }
umask 077

# Print a .env.example line, replacing a trailing CHANGE_ME with a random value. Used by both branches below.
render() {
    case "$1" in
        *=CHANGE_ME) printf '%s%s\n' "${1%CHANGE_ME}" "$(rand)" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

if [ ! -e .env ]; then
    : > .env.tmp
    while IFS= read -r line || [ -n "$line" ]; do
        render "$line" >> .env.tmp
    done < .env.example
    mv .env.tmp .env
    echo "init-env: wrote .env with generated local passwords"
    exit 0
fi

added=0
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
        *=*) key="${line%%=*}" ;;
        *) continue ;;
    esac
    if ! grep -q "^${key}=" .env; then
        [ "$added" -eq 0 ] && printf '\n# --- added by infra/scripts/init-env.sh from .env.example\n' >> .env
        render "$line" >> .env
        added=$((added + 1))
        echo "init-env: added $key"
    fi
done < .env.example
echo "init-env: .env already existed; appended $added missing key(s), changed nothing else"
