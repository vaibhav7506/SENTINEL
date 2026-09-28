"""Explicit operator migration for retention and legacy integration encryption."""

import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.saas.integrations import encrypt_destination
from app.saas.models import AlertChannel


async def configure() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as db, db.begin():
            await db.execute(text("SELECT remove_retention_policy('metrics',if_exists=>true)"))
            await db.execute(
                text(
                    "SELECT add_retention_policy('metrics',"
                    "drop_after=>make_interval(days=>:days),if_not_exists=>true)"
                ),
                {"days": settings.metric_retention_days},
            )
            rows = (await db.scalars(select(AlertChannel).with_for_update())).all()
            for row in rows:
                if not row.destination.startswith("enc:v1:"):
                    row.destination = encrypt_destination(row.destination, row.account_id, settings)
        print("Customer telemetry retention configured; integrations encrypted")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(configure())
