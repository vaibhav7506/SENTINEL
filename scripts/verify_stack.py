"""Read-only smoke checks for the Phase 1 stack; exits nonzero on any failure."""

import json
import os
import subprocess
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
# Parse only local port settings; never emit or load credentials into output.
ports = {}
if (root / ".env").exists():
    for line in (root / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.endswith("_PORT"):
            ports[key] = value


def url(key, default, path):
    port = os.environ.get(key, ports.get(key, str(default)))
    return f"http://127.0.0.1:{port}{path}"


def read(address):
    with urllib.request.urlopen(address, timeout=12) as response:
        return response.read()


checks = {
    "api_health": url("API_PORT", 8000, "/health"),
    "api_readiness": url("API_PORT", 8000, "/ready"),
    "frontend": url("FRONTEND_PORT", 5173, "/"),
    "frontend_api_proxy": url("FRONTEND_PORT", 5173, "/api/ready"),
    "prometheus": url("PROMETHEUS_PORT", 9090, "/-/ready"),
    "grafana": url("GRAFANA_PORT", 4300, "/api/health"),
    "demo_service": url("DEMO_PORT", 8001, "/health"),
}
failed = False
for name, address in checks.items():
    try:
        body = read(address)
        if name in ("api_readiness", "frontend_api_proxy"):
            data = json.loads(body)
            assert data["status"] == "ready"
            assert set(data["dependencies"]) == {"database", "prometheus"}
            assert all(check["status"] == "up" for check in data["dependencies"].values())
        elif name == "frontend":
            assert b'id="root"' in body and b"Sentinel" in body
        elif name in ("api_health", "demo_service"):
            assert json.loads(body)["status"] == "ok"
        elif name == "grafana":
            assert json.loads(body)["database"] == "ok"
        print(f"PASS {name}")
    except Exception as error:
        failed = True
        print(f"FAIL {name}: {type(error).__name__}")
try:
    output = subprocess.check_output(["docker", "compose", "ps", "--format", "json"], cwd=root)
    containers = [json.loads(line) for line in output.decode().splitlines() if line]
    expected = {
        "timescaledb",
        "prometheus",
        "grafana",
        "sentinel-api",
        "sentinel-worker",
        "sentinel-observer",
        "sentinel-frontend",
        "demo-service",
        "sentinel-agent",
    }
    assert {container["Service"] for container in containers} == expected
    assert all(container["State"] == "running" for container in containers)
    assert all(
        container["Health"] == "healthy"
        for container in containers
        if container["Service"] not in {"sentinel-worker", "sentinel-observer"}
    )
    print("PASS nine services running; seven health checks healthy")
except Exception as error:
    failed = True
    print(f"FAIL compose_services: {type(error).__name__}")
raise SystemExit(1 if failed else 0)
