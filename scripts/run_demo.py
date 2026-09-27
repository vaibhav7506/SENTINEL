"""One fixed demo fault with actual observations, scores and alert timing; never force ML output."""

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx

from app.core.config import Settings
from app.core.event_loop import create_event_loop


async def demo(args) -> None:
    settings = Settings(_env_file=ROOT / ".env")
    parsed = urlparse(args.api_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use the local API or an explicit loopback Kubernetes port-forward")
    token = os.getenv("SENTINEL_DEMO_TOKEN") or settings.chaos_control_token.get_secret_value()
    if len(token) < 32:
        raise ValueError("Demo control credential is missing")
    started = datetime.now(UTC)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new evidence path inside the project")
    evidence = {
        "scope": "local API or operator port-forward; fixed demo only",
        "started_at": started.isoformat(),
        "forced_model_output": False,
    }
    experiment_id = None
    async with httpx.AsyncClient(
        base_url=args.api_url,
        timeout=15,
        trust_env=False,
        headers={"Authorization": "Bearer " + token},
    ) as client:
        snapshot = (await client.get("/console/snapshot")).json()
        host = next(h for h in snapshot["hosts"] if h["id"] == args.host)
        if (
            host["environment"] not in {"development", "test", "demo"}
            or host["service_job"] != "demo-service"
            or host["status"] != "healthy"
        ):
            raise ValueError("The fixed demo must have a fresh healthy observed baseline")
        prediction = host["latest_prediction"]
        if (
            prediction is None
            or not 0
            <= (started - datetime.fromisoformat(prediction["predicted_at"])).total_seconds()
            <= 120
        ):
            raise ValueError("Wait for actual feature coverage and fresh inference before the demo")
        evidence["baseline"] = {"observation": host["observation"], "prediction": prediction}
        model = next(m for m in snapshot["models"] if m["id"] == prediction["model_version_id"])
        threshold = model["threshold"]
        evidence["unchanged_threshold"] = threshold
        print(
            "Healthy service observed; actual baseline probability "
            + str(prediction["probability"]),
            flush=True,
        )
        if prediction["probability"] >= threshold:
            print(
                "Baseline risk is already high; it will not be presented as a new advance warning.",
                flush=True,
            )
        try:
            response = await client.post(
                "/chaos/experiments",
                json={
                    "host_id": args.host,
                    "failure_type": "application_latency",
                    "duration_seconds": 45,
                    "latency_ms": 750,
                },
            )
            response.raise_for_status()
            experiment_id = response.json()["experiment_id"]
            deadline = time.monotonic() + 180
            event = None
            while time.monotonic() < deadline:
                rows = (await client.get("/chaos/failure-events")).json()
                event = next((r for r in rows if r["experiment_id"] == experiment_id), None)
                if event and event["recovered_at"]:
                    break
                await asyncio.sleep(3)
            if event is None or not event["recovered_at"]:
                raise RuntimeError("Actual breach/recovery was not confirmed before the deadline")
            # Allow a real post-recovery minute; never insert a synthetic window.
            deadline = time.monotonic() + 120
            while True:
                final = (await client.get("/console/snapshot")).json()
                host_final = next(h for h in final["hosts"] if h["id"] == args.host)
                latest = host_final["latest_prediction"]
                if latest and datetime.fromisoformat(
                    latest["feature_window_end"]
                ) > datetime.fromisoformat(event["recovered_at"]):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("No actual post-recovery feature/prediction arrived")
                await asyncio.sleep(5)
            rows = (await client.get("/predictions?limit=100")).json()
            predictions = [
                p
                for p in rows
                if p["host_id"] == args.host
                and datetime.fromisoformat(p["predicted_at"]) >= started - timedelta(seconds=600)
            ]
            incidents = [
                i
                for i in (await client.get("/incidents?limit=100")).json()
                if i["host_id"] == args.host
            ]
            alert_rows = (await client.get("/alerts?limit=100")).json()
            incident_ids = {i["id"] for i in incidents}
            alerts = [
                {
                    k: a.get(k)
                    for k in (
                        "id",
                        "incident_id",
                        "channel",
                        "status",
                        "created_at",
                        "delivered_at",
                    )
                }
                for a in alert_rows
                if a["incident_id"] in incident_ids
            ]
            onset = datetime.fromisoformat(event["observed_at"])
            useful = []
            for alert in alerts:
                incident = next(i for i in incidents if i["id"] == alert["incident_id"])
                if (
                    alert["delivered_at"]
                    and incident.get("prediction_at")
                    and incident.get("forecast_valid_until")
                ):
                    delivered = datetime.fromisoformat(alert["delivered_at"])
                    if (
                        started
                        <= datetime.fromisoformat(incident["prediction_at"])
                        <= delivered
                        < onset
                        < datetime.fromisoformat(incident["forecast_valid_until"])
                    ):
                        useful.append((onset - delivered).total_seconds())
            evidence.update(
                {
                    "verified_at": datetime.now(UTC).isoformat(),
                    "experiment_id": experiment_id,
                    "failure_event": event,
                    "predictions": predictions,
                    "post_recovery_observation": host_final["observation"],
                    "incidents": incidents,
                    "alerts": alerts,
                    "new_pre_failure_alert_lead_seconds": max(useful) if useful else None,
                    "useful_warning_at_least_60_seconds": bool(useful) and max(useful) >= 60,
                    "outcome": "New receiver-accepted warning before observed degradation"
                    if useful
                    else "No new pre-failure alert; no predictive-success claim",
                    "baseline_already_high": prediction["probability"] >= threshold,
                    "model_limitation": "Frozen held-out recall/F1 are zero",
                }
            )
        finally:
            if experiment_id:
                response = await client.post("/chaos/experiments/" + experiment_id + "/cancel")
                if response.status_code != 200:
                    print(
                        "Cancellation unconfirmed; the independent 45-second TTL still applies.",
                        flush=True,
                    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "outcome": evidence["outcome"],
                "lead_seconds": evidence["new_pre_failure_alert_lead_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--host", choices=["docker-vm", "sentinel-demo"], default="docker-vm")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".runtime/demo" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json"),
    )
    asyncio.run(demo(parser.parse_args()), loop_factory=create_event_loop)
