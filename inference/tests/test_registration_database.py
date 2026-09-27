"""Frozen model import is idempotent and cannot overwrite incompatible provenance."""

import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import EvaluationRun, ModelVersion
from inference.register import register

pytestmark = pytest.mark.skipif(
    os.environ.get("SENTINEL_TEST_DATABASE") != "1", reason="isolated database only"
)


async def test_model_import_is_idempotent_and_mismatch_fails_closed():
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        await register()
        await register()
        async with async_sessionmaker(engine)() as session, session.begin():
            model = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == "20260926T204848-mlp-1bcd8acd")
            )
            assert model is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvaluationRun)
                    .where(EvaluationRun.model_version_id == model.id)
                )
                == 1
            )
            model.training_metadata = {"tampered": True}
        with pytest.raises(ValueError, match="differs"):
            await register()
    finally:
        await engine.dispose()
