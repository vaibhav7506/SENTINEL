# ruff: noqa: E402
"""Verify saved weights, transforms, independent scores and actual DB registration."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import torch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.models import (
    Alert,
    EvaluationRun,
    FailureEvent,
    FeatureWindow,
    Incident,
    ModelVersion,
    Prediction,
    RemediationProposal,
    ServiceObservation,
)
from training.labels import Event, Probe, label_window
from training.runtime import FailureMLP, fit_normalizer, normalize, probabilities
from training.train import (
    evaluate,
    load_dataset,
    load_healthy_training,
    select_threshold,
)


async def verify(target: Path) -> None:
    torch.set_num_threads(1)
    metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    for name, digest in metadata["artifact_sha256"].items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Artifact digest mismatch: " + name)
    dataset = Path(metadata["dataset_reference"])
    if (
        hashlib.sha256((dataset / "report.json").read_bytes()).hexdigest()
        != metadata["dataset_report_sha256"]
    ):
        raise ValueError("Frozen dataset report digest mismatch")
    rows, report = load_dataset(dataset)
    if report["dataset_sha256"] != metadata["dataset_sha256"]:
        raise ValueError("Model/dataset digest mismatch")
    healthy_rows = load_healthy_training(Path(metadata["dataset_reference"]), report, rows)
    normalizer = json.loads((target / "normalizer.json").read_text(encoding="utf-8"))
    training = [r for r in rows if r["split"] == "train"]
    if normalizer != fit_normalizer(np.array([r["feature_values"] for r in training], dtype=float)):
        raise ValueError("Normalizer was not fit exclusively on training rows")
    calibration = json.loads((target / "calibration.json").read_text(encoding="utf-8"))
    saved = torch.load(target / "mlp.pt", map_location="cpu", weights_only=True)
    checkpoint = torch.load(target / "best_checkpoint.pt", map_location="cpu", weights_only=True)
    if checkpoint["epoch"] != metadata["training"]["best_epoch"]:
        raise ValueError("Best checkpoint epoch mismatch")
    for key, value in saved["state_dict"].items():
        if not torch.equal(value, checkpoint["state_dict"][key]):
            raise ValueError("Final weights differ from best validation checkpoint")
    model = FailureMLP(saved["input_width"])
    model.load_state_dict(saved["state_dict"])
    model.eval()
    forest = joblib.load(target / "isolation_forest.joblib")
    if forest.max_samples_ != min(256, len(healthy_rows)):
        raise ValueError("Isolation Forest training sample count mismatch")
    scores = [
        json.loads(line)
        for line in (target / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for partition in ("validation", "test"):
        selected = [r for r in rows if r["split"] == partition]
        values = normalize(
            np.array([r["feature_values"] for r in selected], dtype=float), normalizer
        )
        with torch.no_grad():
            probability = probabilities(model(torch.from_numpy(values)).numpy(), calibration)
        anomaly = -forest.decision_function(values)
        persisted = [r for r in scores if r["split"] == partition]
        np.testing.assert_allclose(
            probability, [r["failure_probability"] for r in persisted], rtol=1e-6
        )
        np.testing.assert_allclose(anomaly, [r["anomaly_score"] for r in persisted], rtol=1e-6)
        for row, score in zip(selected, persisted, strict=True):
            expected = {key: value for key, value in row.items() if key != "feature_values"}
            if any(score.get(key) != value for key, value in expected.items()):
                raise ValueError("Saved score identity differs from its dataset row")
            if score["warning"] != (score["failure_probability"] >= metadata["threshold"]):
                raise ValueError("Saved warning differs from frozen threshold")
        recomputed = evaluate(selected, probability, metadata["threshold"], anomaly)
        if recomputed != metadata["evaluation"][partition]:
            raise ValueError("Evaluation metrics differ from reloaded model scores")
        if partition == "validation":
            threshold = select_threshold(np.array([r["label"] for r in selected]), probability)
            if threshold != metadata["threshold"]:
                raise ValueError("Threshold differs from validation-only selection")
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            registered = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == metadata["version"])
            )
            if not registered or registered.training_metadata != metadata:
                raise ValueError("Model metadata not registered accurately")
            evaluation = await session.scalar(
                select(EvaluationRun).where(EvaluationRun.model_version_id == registered.id)
            )
            if not evaluation or evaluation.metrics != metadata["evaluation"]:
                raise ValueError("Evaluation metadata not registered accurately")
            criterion = report["negative_label_criterion"]
            as_of = datetime.fromisoformat(report["as_of"])
            records = (await session.scalars(select(FailureEvent))).all()
            events = [
                Event(
                    str(e.id),
                    e.host_id,
                    str(e.experiment_id) if e.experiment_id else None,
                    e.failure_type,
                    e.observed_at,
                    e.confirmed_at,
                    e.recovered_at,
                )
                for e in records
                if e.confirmed_at is not None
            ]
            observations = (
                await session.scalars(
                    select(ServiceObservation).where(ServiceObservation.observed_at <= as_of)
                )
            ).all()
            host_probes = {
                host: [
                    Probe(
                        p.observed_at,
                        p.status_code == 200
                        and p.latency_seconds <= criterion["latency_slo_seconds"],
                        criterion,
                    )
                    for p in observations
                    if p.host_id == host
                ]
                for host in {r["host_id"] for r in rows + healthy_rows}
            }
            for row in rows:
                at = datetime.fromisoformat(row["window_end"])
                actual = await session.scalar(
                    select(FeatureWindow).where(
                        FeatureWindow.host_id == row["host_id"],
                        FeatureWindow.window_end == at,
                        FeatureWindow.schema_version == report["feature_schema"]["version"],
                    )
                )
                if (
                    actual is None
                    or actual.feature_values != row["feature_values"]
                    or actual.schema_hash != report["feature_schema"]["hash"]
                    or actual.feature_names != report["feature_schema"]["names"]
                ):
                    raise ValueError("Supervised vector differs from persisted telemetry")
                label = label_window(
                    row["host_id"],
                    at,
                    row["horizon_seconds"],
                    report["recovery_exclusion_seconds"],
                    events,
                    host_probes[row["host_id"]],
                    criterion,
                    as_of,
                    consecutive_breaches=report["failure_confirmation"]["consecutive_breaches"],
                )
                if label.value != row["label"] or label.event_id != row["failure_event_id"]:
                    raise ValueError("Supervised label differs from actual observed evidence")
            for row in healthy_rows:
                host = row["host_id"]
                start, end = (
                    datetime.fromisoformat(row["window_start"]),
                    datetime.fromisoformat(row["window_end"]),
                )
                actual = await session.scalar(
                    select(FeatureWindow).where(
                        FeatureWindow.host_id == host,
                        FeatureWindow.window_end == end,
                        FeatureWindow.schema_version == report["feature_schema"]["version"],
                    )
                )
                if (
                    actual is None
                    or actual.feature_values != row["feature_values"]
                    or actual.schema_hash != report["feature_schema"]["hash"]
                    or actual.feature_names != report["feature_schema"]["names"]
                ):
                    raise ValueError("Healthy training vector differs from persisted telemetry")
                probes = [
                    Probe(
                        p.observed_at,
                        p.status_code == 200
                        and p.latency_seconds <= criterion["latency_slo_seconds"],
                        criterion,
                    )
                    for p in observations
                    if p.host_id == host
                ]
                health = label_window(
                    host,
                    start,
                    int((end - start).total_seconds()),
                    0,
                    events,
                    probes,
                    criterion,
                    as_of,
                )
                if health.value != 0:
                    raise ValueError("Isolation Forest input lacks actual healthy probe evidence")
            future = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in (Prediction, Incident, Alert, RemediationProposal)
            }
            if any(future.values()):
                raise ValueError("Later phase tables unexpectedly populated")
    finally:
        await engine.dispose()
    record = {
        "verified_at": datetime.now(UTC).isoformat(),
        "model_version": metadata["version"],
        "dataset_sha256": report["dataset_sha256"],
        "verified_artifact_count": len(metadata["artifact_sha256"]),
        "reloaded_score_count": len(scores),
        "actually_healthy_isolation_forest_inputs_verified": len(healthy_rows),
        "actual_supervised_feature_and_label_rows_verified": len(rows),
        "validation_only_threshold_verified": True,
        "evaluation_metrics_recomputed": True,
        "database_model_and_evaluation_verified": True,
        "later_phase_table_counts": future,
    }
    (ROOT / "docs" / "phase-5-validation.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.model), loop_factory=create_event_loop)
