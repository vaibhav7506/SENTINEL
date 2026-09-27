"""Local read-only acceptance: dependency failure, live metrics and frozen evidence."""

# ruff: noqa: E402
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from dotenv import dotenv_values

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.main import create_app

REQUIRED = [
    "sentinel_predictions_total",
    "sentinel_high_risk_predictions_total",
    "sentinel_alerts_total",
    "sentinel_alert_failures_total",
    "sentinel_inference_duration_seconds_count",
    "sentinel_feature_generation_duration_seconds_count",
    "sentinel_llm_requests_total",
    "sentinel_llm_failures_total",
    "sentinel_worker_last_success_timestamp",
]


async def verify() -> dict:
    settings = Settings(_env_file=ROOT / ".env")
    broken = settings.model_copy(
        update={
            "postgres_host": "127.0.0.1",
            "postgres_port": 1,
            "prometheus_url": "http://127.0.0.1:9090",
            "readiness_timeout_seconds": 0.3,
        }
    )
    application = create_app(broken)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(application), base_url="http://test", timeout=5
        ) as fixture,
    ):
        assert (await fixture.get("/health")).status_code == 200
        ready = await fixture.get("/ready")
        assert (
            ready.status_code == 503
            and ready.json()["dependencies"]["database"]["status"] == "down"
        )
        data = await fixture.get("/console/snapshot")
        assert data.status_code == 503 and data.json() == {"detail": "Service unavailable"}
        assert "password" not in data.text and data.headers["X-Request-ID"]
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get("http://127.0.0.1:5173/api/console/snapshot")
        response.raise_for_status()
        snapshot = response.json()
        assert (await client.get("http://127.0.0.1:8000/health")).json()["phase"] == "9"
        assert (await client.get("http://127.0.0.1:8000/ready")).status_code == 200
        front = await client.get("http://127.0.0.1:5173/")
        assert "frame-ancestors 'none'" in front.headers["Content-Security-Policy"]
        assert front.headers["X-Content-Type-Options"] == "nosniff"
        assert (
            await client.post("http://127.0.0.1:8000/chaos/experiments", json={})
        ).status_code == 401
        assert (
            await client.get("http://127.0.0.1:8000/console/hosts/nonexistent-fixture")
        ).status_code == 404
        metrics = {}
        for name in REQUIRED:
            query = await client.get("http://127.0.0.1:9090/api/v1/query", params={"query": name})
            query.raise_for_status()
            result = query.json()["data"]["result"]
            assert result, f"Required self metric is not scraped: {name}"
            metrics[name] = [
                {
                    "job": row["metric"]["job"],
                    "worker": row["metric"].get("worker"),
                    "value": float(row["value"][1]),
                }
                for row in result
            ]
        credentials = dotenv_values(ROOT / ".env")
        grafana = await client.get(
            "http://127.0.0.1:4300/api/dashboards/uid/sentinel-self",
            auth=httpx.BasicAuth(
                credentials.get("GRAFANA_ADMIN_USER") or "sentinel",
                credentials["GRAFANA_ADMIN_PASSWORD"] or "",
            ),
        )
        grafana.raise_for_status()
        dashboard = grafana.json()["dashboard"]
        assert len(dashboard["panels"]) == 12
        for panel in dashboard["panels"]:
            for target in panel["targets"]:
                query = await client.get(
                    "http://127.0.0.1:9090/api/v1/query", params={"query": target["expr"]}
                )
                query.raise_for_status()
                assert query.json()["status"] == "success"
    model_path = ROOT / settings.inference_model_path
    metadata = json.loads((model_path / "metadata.json").read_text())
    for name, digest in metadata["artifact_sha256"].items():
        assert hashlib.sha256((model_path / name).read_bytes()).hexdigest() == digest
    frozen = {}
    for phase in (7, 8):
        evidence = json.loads((ROOT / f"docs/phase-{phase}-validation.json").read_text())[
            "evidence"
        ]
        for name, item in evidence.items():
            assert hashlib.sha256((ROOT / "docs" / name).read_bytes()).hexdigest() == item["sha256"]
            frozen[name] = "unchanged"
    return {
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "closed_database_port_fixture": {
            "liveness": 200,
            "readiness": 503,
            "snapshot": 503,
            "live_database_was_not_stopped": True,
        },
        "live_snapshot": snapshot["totals"],
        "required_self_metrics": metrics,
        "grafana_dashboard_panels": len(dashboard["panels"]),
        "grafana_queries": "all accepted by live Prometheus",
        "frontend_security_headers": "passed",
        "unauthenticated_mutation": 401,
        "missing_host": 404,
        "frozen_model_threshold": metadata["threshold"],
        "frozen_model_artifacts": "unchanged",
        "earlier_phase_evidence": frozen,
        "actual_test_recall": metadata["evaluation"]["test"]["recall"],
        "predictive_success_claim": False,
        "phase_10_started": False,
    }


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(verify())
    (ROOT / "docs/phase-9-runtime.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
