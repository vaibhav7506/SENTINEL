#!/bin/sh
set -eu
fail() { printf '%s\n' "Invalid or missing collector setting: $1" >&2; exit 1; }
values="${GRAFANA_CLOUD_REMOTE_WRITE_URL:-}${GRAFANA_CLOUD_METRICS_INSTANCE_ID:-}${GRAFANA_CLOUD_METRICS_WRITE_TOKEN:-}"
[ "$(printf '%s' "$values" | tr -d '\r\n')" = "$values" ] || fail multiline
printf '%s' "${GRAFANA_CLOUD_REMOTE_WRITE_URL:-}" | grep -Eq '^https://[a-z0-9.-]+\.grafana\.net/api/prom/push$' || fail GRAFANA_CLOUD_REMOTE_WRITE_URL
printf '%s' "${GRAFANA_CLOUD_METRICS_INSTANCE_ID:-}" | grep -Eq '^[0-9]+$' || fail GRAFANA_CLOUD_METRICS_INSTANCE_ID
[ -n "${GRAFANA_CLOUD_METRICS_WRITE_TOKEN:-}" ] || fail GRAFANA_CLOUD_METRICS_WRITE_TOKEN
exec /bin/alloy run --server.http.listen-addr=0.0.0.0:12345 \
  --storage.path=/var/lib/alloy/data /etc/alloy/config.alloy
