"""Opt-in integration test against an isolated, migrated PostgreSQL database."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.features.engineering import generate_features
from app.features.prometheus import HostSeries
from app.features.schema import FeatureSchema
from app.models import FeatureWindow, Host
from app.workers.features import persist_window


@pytest.mark.skipif(
    os.getenv("SENTINEL_TEST_DATABASE") != "1", reason="Requires isolated PostgreSQL test database"
)
async def test_real_hypertable_immutable_retries_and_atomic_rollback():
    settings = Settings()
    assert settings.postgres_db.startswith("sentinel_phase3_test_")
    engine = create_database_engine(settings)
    schema = FeatureSchema()
    end = datetime(2026, 1, 1, tzinfo=UTC)
    host = HostSeries(
        "Integration host",
        "test",
        {"cpu_usage_percent": [(end.timestamp() - 30, 20), (end.timestamp(), 40)]},
    )
    result = generate_features(host.series, end.timestamp(), schema)
    try:
        assert await persist_window(engine, "integration", host, end, schema, result, None)
        host.series["cpu_usage_percent"][-1] = (end.timestamp(), 90)
        changed = generate_features(host.series, end.timestamp(), schema)
        assert not await persist_window(engine, "integration", host, end, schema, changed, None)
        async with async_sessionmaker(engine)() as session:
            row = (await session.scalars(select(FeatureWindow))).one()
            assert row.features["cpu_usage_percent.latest"] == 40
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name='feature_windows'"
                    )
                )
                == 1
            )
        # A DB constraint failure rolls back the host update in the same transaction.
        invalid_schema = FeatureSchema()
        object.__setattr__(invalid_schema, "window_seconds", -900)
        with pytest.raises(IntegrityError):
            await persist_window(
                engine,
                "rollback-host",
                host,
                end,
                invalid_schema,
                result,
                None,
            )
        async with async_sessionmaker(engine)() as session:
            assert await session.get(Host, "rollback-host") is None
    finally:
        await engine.dispose()
