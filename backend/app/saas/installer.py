"""Serve the fixed agent source bundle and a Linux installer; no tokens in artifacts."""

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter(tags=["agent installation"])
DIRECTORY = Path(__file__).resolve().parent


@router.get("/agent/package.tar.gz", response_class=FileResponse)
async def package() -> FileResponse:
    return FileResponse(
        DIRECTORY / "agent_bundle.tar.gz",
        media_type="application/gzip",
        filename="sentinel-agent.tar.gz",
    )


@router.get("/agent/install.sh", response_class=PlainTextResponse)
async def installer() -> PlainTextResponse:
    checksum = json.loads((DIRECTORY / "agent_bundle.json").read_text())["sha256"]
    script = r"""#!/usr/bin/env bash
set -euo pipefail
umask 077
server=""
enrollment_token=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --server) server="$2"; shift 2 ;;
    --server=*) server="${1#*=}"; shift ;;
    --enrollment-token) enrollment_token="$2"; shift 2 ;;
    --enrollment-token=*) enrollment_token="${1#*=}"; shift ;;
    *) echo "Unknown install option" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -ne 0 ] || [ "$(uname -s)" != Linux ]; then
  echo "Run this Linux installer with sudo." >&2; exit 2
fi
case "$server" in
  https://*|http://127.0.0.1:*|http://localhost:*) ;;
  *) echo "Use HTTPS or local loopback" >&2; exit 2;;
esac
case "$enrollment_token" in enr_*) ;; *) echo "Enrollment token required" >&2; exit 2;; esac
if [ -f /etc/sentinel/agent.toml ]; then
  echo "An identity already exists. Restart sentinel-agent instead of re-enrolling."; exit 0
fi
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
curl -fsSL "${server%/}/agent/package.tar.gz" -o "$temporary/agent.tar.gz"
printf '__CHECKSUM__  %s\n' "$temporary/agent.tar.gz" | sha256sum -c - >/dev/null
if ! id sentinel-agent >/dev/null 2>&1; then
  useradd --system --home-dir /opt/sentinel --shell /usr/sbin/nologin sentinel-agent
fi
install -d -m 0755 /opt/sentinel /opt/sentinel/agent /opt/sentinel/bin /opt/sentinel/python
install -d -m 0700 -o sentinel-agent -g sentinel-agent /etc/sentinel
tar -xzf "$temporary/agent.tar.gz" -C /opt/sentinel/agent --no-same-owner
curl -fsSL https://astral.sh/uv/0.12.19/install.sh -o "$temporary/uv-install.sh"
UV_INSTALL_DIR=/opt/sentinel/bin UV_NO_MODIFY_PATH=1 sh "$temporary/uv-install.sh" >/dev/null
export UV_PYTHON_INSTALL_DIR=/opt/sentinel/python
/opt/sentinel/bin/uv sync --project /opt/sentinel/agent --frozen --no-dev
chmod -R a+rX /opt/sentinel/agent /opt/sentinel/python
runuser -u sentinel-agent -- /opt/sentinel/agent/.venv/bin/python -m sentinel_agent.identity \
  --server "$server" --enrollment-token "$enrollment_token" \
  --identity-path /etc/sentinel/agent.toml
unset enrollment_token
cat > /etc/systemd/system/sentinel-agent.service <<'SERVICE'
[Unit]
Description=Sentinel telemetry agent
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=sentinel-agent
Group=sentinel-agent
WorkingDirectory=/opt/sentinel/agent
Environment=AGENT_IDENTITY_PATH=/etc/sentinel/agent.toml
ExecStart=/opt/sentinel/agent/.venv/bin/python -m sentinel_agent.main
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
SERVICE
chmod 0644 /etc/systemd/system/sentinel-agent.service
systemctl daemon-reload
systemctl enable --now sentinel-agent
echo "Sentinel agent started. Identity is reused on restart."
""".replace("__CHECKSUM__", checksum)
    return PlainTextResponse(
        script, media_type="text/x-shellscript", headers={"Cache-Control": "no-store"}
    )
