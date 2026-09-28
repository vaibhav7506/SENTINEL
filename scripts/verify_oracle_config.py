"""Check the standalone managed Oracle profile without exposing private variables."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.oracle")
    args = parser.parse_args()
    process = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "sentinel-oracle",
            "--env-file",
            str(args.env_file),
            "--project-directory",
            str(ROOT),
            "-f",
            str(ROOT / "infra/oracle/compose.yaml"),
            "--profile",
            "ops",
            "--profile",
            "observability",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise SystemExit("Oracle Compose configuration failed; check settings privately")
    config = json.loads(process.stdout)
    services = config["services"]
    assert set(services) == {
        "redis",
        "migrate",
        "register",
        "sentinel-api",
        "sentinel-push-worker",
        "sentinel-beat",
        "sentinel-frontend",
        "edge",
        "alloy",
    }
    memory = 0
    for name, service in services.items():
        assert service["platform"] == "linux/arm64", name
        memory += int(service.get("mem_limit", 0))
        for port in service.get("ports", []):
            if name == "edge":
                assert port["target"] in {80, 443}
            else:
                raise AssertionError("Only the edge may publish ports: " + name)
    assert memory <= 12 * 1024**3
    for name in ("sentinel-api", "sentinel-push-worker", "sentinel-beat", "migrate", "register"):
        env = services[name]["environment"]
        assert env["ENVIRONMENT"] == "production"
        assert env["SAAS_ENABLED"] == "true"
        assert env["CHAOS_ENABLED"] == "false"
        assert env["RUNBOOKOS_ENABLED"] == "false"
        assert env["PROMETHEUS_READINESS_MODE"] == "disabled"
        assert (
            "sslmode=require" in env["DATABASE_URL"] or "sslmode=verify-full" in env["DATABASE_URL"]
        )
        assert env["SAAS_PUBLIC_API_URL"].startswith("https://")
        assert json.loads(env["SAAS_ALLOWED_ORIGINS"]) == [
            env["SAAS_PUBLIC_API_URL"].removesuffix("/api")
        ]
    for name in ("register", "sentinel-push-worker", "sentinel-beat"):
        build = services[name]["build"]
        assert build["dockerfile"] == "queue/Dockerfile.arm64"
    assert services["register"]["command"][0] == "/app/inference/arm64/.venv/bin/python"
    for name in ("sentinel-api", "sentinel-push-worker", "sentinel-frontend"):
        assert services[name]["image"].split("@")[0].split(":")[0].endswith("-arm64")
    assert not services["redis"].get("ports")
    assert services["alloy"]["image"].startswith("grafana/alloy@sha256:")
    edge = services["edge"]
    assert edge["read_only"] and edge["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in edge["security_opt"]
    print(
        json.dumps(
            {
                "status": "passed",
                "services": 9,
                "architecture": "arm64",
                "configured_memory_limit_gib": round(memory / 1024**3, 2),
                "public_service": "edge",
                "deployed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
