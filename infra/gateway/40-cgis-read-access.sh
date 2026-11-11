#!/bin/sh
# Select the read-route access mode at container start (fail closed on unknown values).
set -eu
mode="${GATEWAY_READ_ACCESS:-public}"
case "$mode" in
    public|protected) ;;
    *) echo "40-cgis-read-access: GATEWAY_READ_ACCESS must be 'public' or 'protected', got '$mode'" >&2; exit 1 ;;
esac
cp "/etc/nginx/cgis/read-access.$mode.conf" /etc/nginx/cgis/read-access.conf
echo "40-cgis-read-access: read routes are $mode"
