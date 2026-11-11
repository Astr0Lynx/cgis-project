#!/bin/sh
# Runs at gateway start (nginx image /docker-entrypoint.d/): writes the runtime nginx snippets.
#   resolver.conf     DNS server from /etc/resolv.conf (Docker 127.0.0.11, podman aardvark, ...)
#   read-access.conf  GATEWAY_READ_ACCESS=public (default) or protected; anything else fails closed.
set -eu
dir=/etc/nginx/cgis

ns=$(awk '/^nameserver/ {print ($2 ~ /:/ ? "[" $2 "]" : $2); exit}' /etc/resolv.conf)
echo "resolver ${ns:-127.0.0.11} valid=10s ipv6=off;" > "$dir/resolver.conf"

mode="${GATEWAY_READ_ACCESS:-public}"
case "$mode" in
    public)
        # /ogc/, /tiles/ and /api/ read routes are anonymous; admin routes are protected by the app API.
        : > "$dir/read-access.conf" ;;
    protected)
        # A signed-in user or personal access token is required (app-api GET /auth/verify).
        cat > "$dir/read-access.conf" <<'CONF'
auth_request /_cgis_auth;
error_page 401 = @cgis_401;
error_page 403 = @cgis_403;
error_page 500 502 504 = @app_api_unavailable;
CONF
        ;;
    *) echo "40-cgis-gateway: GATEWAY_READ_ACCESS must be 'public' or 'protected', got '$mode'" >&2; exit 1 ;;
esac
echo "40-cgis-gateway: resolver ${ns:-127.0.0.11}; read routes are $mode"
