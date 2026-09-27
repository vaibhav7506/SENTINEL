# ruff: noqa: E402
"""Run bounded local demo experiments after a fully observed healthy baseline."""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import ServiceObservation


async def main(
    output: Path = ROOT / "docs" / "phase-4-campaign.json",
    kinds: tuple[str, ...] = ("application_latency", "http_error"),
    baseline_consecutive_breaches: int = 1,
) -> None:
    settings = Settings(_env_file=ROOT / ".env")
    if not settings.chaos_enabled or settings.environment not in {
        "development",
        "test",
        "demo",
    }:
        raise SystemExit("Enable the allowlisted local demo explicitly first")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    port = int(values.get("API_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid API port")
    engine = create_database_engine(settings)
    baseline_seconds = settings.prediction_horizon_seconds + 90
    mode = "healthy" if baseline_consecutive_breaches == 1 else "no sustained degradation"
    print(
        f"Waiting for {baseline_seconds}s of actual {mode} probe coverage before injecting",
        flush=True,
    )
    next_notice = 0.0
    campaign_started = datetime.now(UTC) - timedelta(seconds=baseline_seconds * 2)
    try:
        while True:
            async with async_sessionmaker(engine)() as session:
                probes = list(
                    (
                        await session.scalars(
                            select(ServiceObservation)
                            .where(
                                ServiceObservation.host_id == "docker-vm",
                                ServiceObservation.observed_at >= campaign_started,
                            )
                            .order_by(ServiceObservation.observed_at)
                        )
                    ).all()
                )
            tail = []
            breach_streak = []
            for probe in probes:
                if probe.breached:
                    breach_streak.append(probe)
                    if len(breach_streak) >= baseline_consecutive_breaches:
                        recent = breach_streak[-baseline_consecutive_breaches:]
                        if (recent[-1].observed_at - recent[0].observed_at).total_seconds() <= 15:
                            tail = []
                            continue
                else:
                    breach_streak = []
                if tail and (probe.observed_at - tail[-1].observed_at).total_seconds() > 10:
                    tail = []
                tail.append(probe)
            span = (tail[-1].observed_at - tail[0].observed_at).total_seconds() if tail else 0
            fresh = bool(tail) and (datetime.now(UTC) - tail[-1].observed_at).total_seconds() <= 10
            if span >= baseline_seconds and fresh:
                probes = tail
                break
            if time.monotonic() >= next_notice:
                print(
                    f"Observed baseline: {span:.0f}/{baseline_seconds}s "
                    f"({len(tail)} covered probes without confirmed degradation)",
                    flush=True,
                )
                next_notice = time.monotonic() + 60
            await asyncio.sleep(5)
        results = []
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=10,
            headers={"Authorization": "Bearer " + settings.chaos_control_token.get_secret_value()},
        ) as client:
            for kind in kinds:
                response = await client.post(
                    "/chaos/experiments",
                    json={
                        "failure_type": kind,
                        "duration_seconds": 45,
                        "latency_ms": 750,
                    },
                )
                response.raise_for_status()
                experiment = response.json()
                identifier = experiment["experiment_id"]
                print(
                    f"Started {kind} experiment {identifier}; bounded to 45s",
                    flush=True,
                )
                deadline = time.monotonic() + 120
                try:
                    while time.monotonic() < deadline:
                        rows = (await client.get("/chaos/experiments")).json()
                        events = (await client.get("/chaos/failure-events")).json()
                        row = next(row for row in rows if row["id"] == identifier)
                        event = next(
                            (event for event in events if event["experiment_id"] == identifier),
                            None,
                        )
                        if row["ended_at"] and event and event["recovered_at"]:
                            results.append({"experiment": row, "failure_event": event})
                            print(
                                f"Confirmed actual breach and recovery for {kind}",
                                flush=True,
                            )
                            break
                        await asyncio.sleep(3)
                    else:
                        raise RuntimeError(
                            "Experiment did not produce a confirmed breach and recovery"
                        )
                finally:
                    # A cancelled campaign never needs to leave its controlled fault running.
                    await client.post(f"/chaos/experiments/{identifier}/cancel")
                await asyncio.sleep(15)
        record = {
            "verified_at": datetime.now(UTC).isoformat(),
            "baseline_seconds": span,
            "baseline_probe_count": len(probes),
            "baseline_consecutive_breaches": baseline_consecutive_breaches,
            "prediction_horizon_seconds": settings.prediction_horizon_seconds,
            "results": results,
        }
        output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(
            "PASS two real experiments, objective failure events, and observed recovery",
            flush=True,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
