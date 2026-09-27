"""Collect the predeclared final held-out campaign after actual healthy coverage."""

import asyncio
import json
from datetime import UTC, datetime

from run_chaos_campaign import ROOT
from run_chaos_campaign import main as campaign

from app.core.event_loop import create_event_loop


async def main() -> None:
    plan = json.loads((ROOT / "docs" / "phase-5-split-plan.json").read_text(encoding="utf-8"))
    target = datetime.fromisoformat(plan["test_campaign_not_before"])
    while datetime.now(UTC) < target:
        await asyncio.sleep(min(30, (target - datetime.now(UTC)).total_seconds()))
    await campaign(
        ROOT / "docs" / "phase-5-final-test-campaign.json",
        ("http_error", "application_latency"),
        baseline_consecutive_breaches=3,
    )


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
