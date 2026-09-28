#!/bin/sh
set -eu
umask 077
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ -f "$root/.env.oracle" ] || { echo "Create private .env.oracle first." >&2; exit 2; }
for argument in "$@"; do
  case "$argument" in
    up|build|run|create|start|restart)
      case "$(uname -m)" in
        aarch64|arm64) ;;
        *) echo "This profile requires an Oracle Ampere A1 ARM64 VM." >&2; exit 2 ;;
      esac
      break ;;
  esac
done
exec docker compose --project-directory "$root" --project-name sentinel-oracle --env-file "$root/.env.oracle" -f "$root/infra/oracle/compose.yaml" "$@"
