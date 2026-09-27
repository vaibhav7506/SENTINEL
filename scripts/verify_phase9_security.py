"""Screen saved evidence, application sources and local logs without printing credentials."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    configuration = dotenv_values(ROOT / ".env")
    secrets = [
        value
        for key, value in configuration.items()
        if value
        and len(value) >= 8
        and any(marker in key for marker in ("PASSWORD", "TOKEN", "API_KEY"))
    ]
    paths = list((ROOT / "docs").glob("*.json")) + list((ROOT / "docs").glob("*.jsonl"))
    paths += list((ROOT / ".runtime/phase9").glob("*.log"))
    paths += list((ROOT / "scripts").glob("*.py"))
    for folder in ["backend/app", "demo-service/app", "agent/sentinel_agent", "frontend/src"]:
        paths += [
            path
            for path in (ROOT / folder).rglob("*")
            if path.suffix in {".py", ".ts", ".tsx", ".json"}
        ]
    paths += list((ROOT / "inference").glob("*.py"))
    for path in paths:
        data = path.read_text(encoding="utf-8")
        if any(secret in data for secret in secrets):
            raise RuntimeError(f"Configured credential found in {path.name}; value withheld")
    for service in ["sentinel-api", "sentinel-worker", "sentinel-observer", "demo-service"]:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--tail", "200", service],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        if any(secret in result.stdout + result.stderr for secret in secrets):
            raise RuntimeError(f"Configured credential found in {service} logs; value withheld")
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "configuration_secrets_screened": len(secrets),
        "files_screened": len(paths),
        "container_log_streams_screened": 4,
        "credential_matches": 0,
        "scope": "Screening covers configured nonempty credentials only",
    }
    (ROOT / "docs/phase-9-secret-screening.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
