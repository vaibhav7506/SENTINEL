# ruff: noqa: E402
"""Report actual collection freshness until the bounded campaign deadline."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import FailureEvent, FeatureWindow, ServiceObservation


async def main() -> None:
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    plan = json.loads((ROOT / "docs" / "phase-5-split-plan.json").read_text(encoding="utf-8"))
    until = datetime.fromisoformat(plan["test_campaign_not_before"]) + timedelta(minutes=6)
    try:
        while datetime.now(UTC) < until:
            async with async_sessionmaker(engine)() as session:
                features = (
                    await session.execute(
                        select(func.count(), func.max(FeatureWindow.window_end)).where(
                            FeatureWindow.host_id == "docker-vm"
                        )
                    )
                ).one()
                probes = (
                    await session.execute(
                        select(func.count(), func.max(ServiceObservation.observed_at))
                    )
                ).one()
                failures = await session.scalar(select(func.count()).select_from(FailureEvent))
            print(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "docker_feature_windows": features[0],
                        "latest_feature": features[1].isoformat(),
                        "actual_probes": probes[0],
                        "latest_probe": probes[1].isoformat(),
                        "confirmed_failures": failures,
                    }
                ),
                flush=True,
            )
            await asyncio.sleep(55)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=create_event_loop)
