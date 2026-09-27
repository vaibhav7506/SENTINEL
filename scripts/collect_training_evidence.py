"""Collect three independent chronological cohorts using bounded local faults."""

import asyncio
import ctypes
import json
from datetime import UTC, datetime, timedelta

from run_chaos_campaign import ROOT
from run_chaos_campaign import main as campaign

from app.core.config import Settings
from app.core.event_loop import create_event_loop


async def main() -> None:
    settings = Settings(_env_file=ROOT / ".env")
    now = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    cohort = settings.feature_window_seconds + settings.prediction_horizon_seconds + 300
    embargo = settings.prediction_horizon_seconds + 300
    train_fault = now + timedelta(seconds=cohort)
    train_boundary = train_fault + timedelta(seconds=embargo)
    validation_fault = train_boundary + timedelta(seconds=cohort)
    test_boundary = validation_fault + timedelta(seconds=embargo)
    test_fault = test_boundary + timedelta(seconds=cohort)
    output = ROOT / "artifacts" / "campaigns" / now.strftime("%Y%m%dT%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    plan = {
        "declared_at": datetime.now(UTC).isoformat(),
        "train_boundary": train_boundary.isoformat(),
        "test_boundary": test_boundary.isoformat(),
        "training_campaign_not_before": train_fault.isoformat(),
        "validation_campaign_not_before": validation_fault.isoformat(),
        "test_campaign_not_before": test_fault.isoformat(),
        "method": "predeclared chronological partitions with interval purge",
    }
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print("Declared split plan: " + str(output / "plan.json"), flush=True)
    # This request is tied to this thread and automatically ends if the process exits.
    if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
        raise RuntimeError("Windows rejected collection sleep prevention")
    try:
        stages = (
            ("train", train_fault, ("application_latency", "http_error")),
            ("validation", validation_fault, ("application_latency", "http_error")),
            ("test", test_fault, ("http_error", "application_latency")),
        )
        for name, target, kinds in stages:
            while datetime.now(UTC) < target:
                await asyncio.sleep(min(30, (target - datetime.now(UTC)).total_seconds()))
            await campaign(output / (name + ".json"), kinds, baseline_consecutive_breaches=3)
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    print("PASS three real campaigns; build dataset using the declared plan", flush=True)


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
