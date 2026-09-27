"""Read-only telemetry acceptance checks; run load_demo.py separately first."""

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
values = {}
for line in (root / ".env").read_text().splitlines():
    key, sep, value = line.partition("=")
    if sep and not line.startswith("#"):
        values[key] = os.environ.get(key, value)
prom = "http://127.0.0.1:" + values.get("PROMETHEUS_PORT", "9090")
grafana = "http://127.0.0.1:" + values.get("GRAFANA_PORT", "4300")
credentials = (values["GRAFANA_ADMIN_USER"] + ":" + values["GRAFANA_ADMIN_PASSWORD"]).encode()
headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode()}


def get(url, authenticated=False):
    request = urllib.request.Request(url, headers=headers if authenticated else {})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def query(expr):
    return get(prom + "/api/v1/query?" + urllib.parse.urlencode({"query": expr}))["data"]["result"]


checks = {}
targets = get(prom + "/api/v1/targets")["data"]["activeTargets"]
required = {"sentinel-agent", "sentinel-api", "sentinel-worker", "demo-service"}
checks["required_targets_up"] = all(
    any(t["labels"]["job"] == job and t["health"] == "up" for t in targets) for job in required
)
local = [t for t in targets if t["labels"]["job"] == "sentinel-local-agent"]
checks["windows_agent_up"] = bool(local and local[0]["health"] == "up")
for name in (
    "cpu_usage_percent",
    "memory_usage_percent",
    "disk_read_bytes_per_second",
    "network_receive_bytes_per_second",
    "process_count",
    "uptime_seconds",
):
    checks["real_host_" + name] = bool(query("sentinel_host_" + name))
for name in (
    "sentinel_http_requests_total",
    "sentinel_http_request_duration_seconds_count",
    "sentinel_http_errors_total",
    "sentinel_http_inflight_requests",
):
    checks["demo_" + name] = bool(query(name + '{job="demo-service"}'))
now = time.time()
for name in ("cpu_usage_percent", "memory_usage_percent"):
    result = get(
        prom
        + "/api/v1/query_range?"
        + urllib.parse.urlencode(
            {
                "query": "sentinel_host_" + name,
                "start": now - 180,
                "end": now,
                "step": 15,
            }
        )
    )["data"]["result"]
    checks["changing_" + name] = any(
        len({value[1] for value in series["values"]}) > 1 for series in result
    )
for uid in ("sentinel-hosts", "sentinel-application", "sentinel-self"):
    dashboard = get(grafana + "/api/dashboards/uid/" + uid, True)["dashboard"]
    populated = 0
    for panel in dashboard["panels"]:
        expr = panel["targets"][0]["expr"].replace("$host", ".*")
        payload = {
            "from": str(int((now - 180) * 1000)),
            "to": str(int(now * 1000)),
            "queries": [
                {
                    "refId": "A",
                    "expr": expr,
                    "datasource": {"uid": "sentinel-prometheus", "type": "prometheus"},
                    "intervalMs": 15000,
                    "maxDataPoints": 100,
                }
            ],
        }
        request = urllib.request.Request(
            grafana + "/api/ds/query",
            data=json.dumps(payload).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)["results"]["A"]
        frames = result.get("frames", [])
        has_numbers = any(
            any(value is not None for value in frame["data"]["values"][i])
            for frame in frames
            for i, field in enumerate(frame["schema"]["fields"])
            if field["type"] == "number"
        )
        populated += int(has_numbers)
    checks["grafana_" + uid] = populated == len(dashboard["panels"])
    print(
        f"Grafana {dashboard['title']}: {populated}/{len(dashboard['panels'])} "
        "panels returned numeric data"
    )
report = {"verified_at": now, "checks": checks}
(root / "docs" / "phase-2-validation.json").write_text(json.dumps(report, indent=2) + "\n")
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
raise SystemExit(0 if all(checks.values()) else 1)
