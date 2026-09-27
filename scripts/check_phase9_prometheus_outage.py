"""Brief authorized local Prometheus outage; always restore the dependency."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    try:
        subprocess.run(["docker", "compose", "stop", "prometheus"], cwd=ROOT, check=True)
        with httpx.Client(timeout=15) as client:
            assert client.get("http://127.0.0.1:8000/health").status_code == 200
            ready = client.get("http://127.0.0.1:8000/ready")
            assert (
                ready.status_code == 503
                and ready.json()["dependencies"]["prometheus"]["status"] == "down"
            )
            host = client.get("http://127.0.0.1:8000/console/hosts/docker-vm")
            host.raise_for_status()
            telemetry = host.json()["telemetry"]
            assert telemetry["status"] == "unavailable" and telemetry["series"] == []
    finally:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--no-build", "--wait", "prometheus"],
            cwd=ROOT,
            check=True,
        )
    with httpx.Client(timeout=15) as client:
        assert client.get("http://127.0.0.1:8000/ready").status_code == 200
        restored = client.get("http://127.0.0.1:8000/console/hosts/docker-vm")
        restored.raise_for_status()
        assert restored.json()["telemetry"]["status"] == "available"
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "prometheus_stopped": {
            "health": 200,
            "ready": 503,
            "telemetry": "unavailable",
            "samples": [],
        },
        "prometheus_restored": {"ready": 200, "telemetry": "available"},
        "no_synthetic_data_or_outage_claim": True,
    }
    (ROOT / "docs/phase-9-prometheus-outage.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
