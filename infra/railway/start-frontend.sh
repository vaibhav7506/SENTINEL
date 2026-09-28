#!/bin/sh
set -eu
umask 077
fail() { printf '%s\n' "Invalid or missing Railway frontend setting: $1" >&2; exit 1; }
PORT=${PORT:-8080}
API_UPSTREAM_HOST=${API_UPSTREAM_HOST:-sentinel-api.railway.internal}
API_UPSTREAM_PORT=${API_UPSTREAM_PORT:-8000}
CONSOLE_USERNAME=${CONSOLE_USERNAME:-sentinel}
single_line_values="$API_UPSTREAM_HOST$CONSOLE_USERNAME${API_READ_TOKEN:-}${CONSOLE_PASSWORD_HASH:-}"
[ "$(printf '%s' "$single_line_values" | tr -d '\r\n')" = "$single_line_values" ] || fail multiline
case "$PORT:$API_UPSTREAM_PORT" in *[!0-9:]*|:*|*:) fail ports ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || fail PORT
[ "$API_UPSTREAM_PORT" -ge 1024 ] && [ "$API_UPSTREAM_PORT" -le 65535 ] || fail API_UPSTREAM_PORT
printf '%s' "$API_UPSTREAM_HOST" | grep -Eq '^[a-z0-9][a-z0-9.-]*\.railway\.internal$' || fail API_UPSTREAM_HOST
printf '%s' "$CONSOLE_USERNAME" | grep -Eq '^[A-Za-z0-9_-]{1,64}$' || fail CONSOLE_USERNAME
printf '%s' "${API_READ_TOKEN:-}" | grep -Eq '^[A-Za-z0-9_-]{32,256}$' || fail API_READ_TOKEN
printf '%s' "${CONSOLE_PASSWORD_HASH:-}" | grep -Eq '^\$2[aby]\$(1[0-9]|2[0-9]|3[01])\$[A-Za-z0-9./]{53}$' || fail CONSOLE_PASSWORD_HASH
RUNTIME_RESOLVER=$(awk '$1 == "nameserver" { print $2; exit }' /etc/resolv.conf)
case "$RUNTIME_RESOLVER" in
  ''|*[!0-9a-fA-F.:]*) fail resolver ;;
  *:*) RUNTIME_RESOLVER="[$RUNTIME_RESOLVER]" ;;
esac
RUNTIME_DIRECTORY=$(mktemp -d /tmp/sentinel-railway.XXXXXX)
printf '%s:%s\n' "$CONSOLE_USERNAME" "$CONSOLE_PASSWORD_HASH" > "$RUNTIME_DIRECTORY/htpasswd"
export PORT API_UPSTREAM_HOST API_UPSTREAM_PORT API_READ_TOKEN RUNTIME_RESOLVER RUNTIME_DIRECTORY
envsubst '${PORT} ${API_UPSTREAM_HOST} ${API_UPSTREAM_PORT} ${API_READ_TOKEN} ${RUNTIME_RESOLVER} ${RUNTIME_DIRECTORY}' \
  < /etc/sentinel/nginx.conf.template > "$RUNTIME_DIRECTORY/nginx.conf"
unset API_READ_TOKEN CONSOLE_PASSWORD_HASH
nginx -t -q -c "$RUNTIME_DIRECTORY/nginx.conf"
exec nginx -c "$RUNTIME_DIRECTORY/nginx.conf" -g 'daemon off;'
