"""Collect an allowlisted bounded campaign at a declared future UTC time."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from run_chaos_campaign import ROOT
from run_chaos_campaign import main as campaign

from app.core.event_loop import create_event_loop


async def collect(at: datetime, output: Path) -> None:
    if at.tzinfo is None or not 0 < (at - datetime.now(UTC)).total_seconds() <= 10800:
        raise ValueError("Campaign time must be aware and within three hours")
    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("Campaign evidence must remain in the workspace")
    while datetime.now(UTC) < at:
        await asyncio.sleep(min(30, (at - datetime.now(UTC)).total_seconds()))
    await campaign(output, baseline_consecutive_breaches=3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--at")
    group.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    at = (
        args.at
        or json.loads(args.plan.read_text(encoding="utf-8"))["validation_campaign_not_before"]
    )
    asyncio.run(collect(datetime.fromisoformat(at), args.output), loop_factory=create_event_loop)
