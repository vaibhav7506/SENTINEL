"""Idempotently import the verified historical model; never train or invent live scores."""

import asyncio
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import EvaluationRun, ModelVersion
from inference.scoring import ROOT, Scorer


async def register() -> None:
    settings = Settings()
    scorer = Scorer(ROOT / settings.inference_model_path)
    metadata = scorer.metadata
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(9274010)"))
            existing = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == metadata["version"])
            )
            if existing is not None:
                if existing.training_metadata != metadata:
                    raise ValueError("Existing model metadata differs from frozen artifacts")
                return
            model = ModelVersion(
                version=metadata["version"],
                schema_version=metadata["feature_schema"]["version"],
                schema_hash=metadata["feature_schema"]["hash"],
                artifact_uri=settings.inference_model_path,
                threshold=metadata["threshold"],
                training_metadata=metadata,
            )
            session.add(model)
            await session.flush()
            session.add(
                EvaluationRun(
                    model_version_id=model.id,
                    dataset_reference=metadata["dataset_reference"],
                    split_definition=metadata["split"],
                    metrics=metadata["evaluation"],
                    completed_at=datetime.fromisoformat(metadata["trained_at"]),
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(register(), loop_factory=create_event_loop)
