# ruff: noqa: E402
"""Build an evidence-based dataset from persisted features and observed events."""

import argparse
import asyncio
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.features.schema import FeatureSchema
from app.models import (
    ChaosExperiment,
    FailureEvent,
    FeatureWindow,
    ServiceObservation,
)
from training.labels import (
    Event,
    Probe,
    chronological_split,
    grouped_split,
    label_window,
)


async def build(
    horizon: int | None,
    output: Path,
    split_plan: Path | None = None,
    summary: Path | None = None,
) -> None:
    settings = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    horizon = horizon if horizon is not None else settings.prediction_horizon_seconds
    if not 30 <= horizon <= 3600:
        raise ValueError("Horizon must be between 30 and 3600 seconds")
    schema = FeatureSchema(window_seconds=settings.feature_window_seconds)
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            # Labels use only confirmed observations visible in this DB snapshot.
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            as_of = await session.scalar(text("SELECT transaction_timestamp()"))
            assert isinstance(as_of, datetime)
            windows = list(
                (
                    await session.scalars(
                        select(FeatureWindow).order_by(
                            FeatureWindow.window_end, FeatureWindow.host_id
                        )
                    )
                ).all()
            )
            records = list((await session.scalars(select(FailureEvent))).all())
            observations = list(
                (
                    await session.scalars(
                        select(ServiceObservation).order_by(ServiceObservation.observed_at)
                    )
                ).all()
            )
            experiments = list((await session.scalars(select(ChaosExperiment))).all())
        events = [
            Event(
                str(row.id),
                row.host_id,
                str(row.experiment_id) if row.experiment_id else None,
                row.failure_type,
                row.observed_at,
                row.confirmed_at,
                row.recovered_at,
            )
            for row in records
            if row.confirmed_at is not None
        ]
        criteria = {
            "latency_slo_seconds": settings.observation_latency_slo_seconds,
            "http_error_status_min": 500,
            "probe": "/work?units=100000",
        }
        probes: dict[str, list[Probe]] = {}
        for observation in observations:
            # Re-evaluate actual status/latency under the explicit dataset SLO.
            healthy = (
                observation.status_code == 200
                and observation.latency_seconds <= settings.observation_latency_slo_seconds
            )
            probes.setdefault(observation.host_id, []).append(
                Probe(observation.observed_at, healthy, criteria)
            )
        skipped: Counter[str] = Counter()
        rows: list[dict[str, Any]] = []
        healthy_inputs: list[dict[str, Any]] = []
        for window in windows:
            if window.window_end > as_of:
                skipped["future_feature_window"] += 1
                continue
            if (
                window.schema_version != schema.version
                or window.schema_hash != schema.digest
                or set(window.features) != set(schema.names)
                or window.feature_names != list(schema.names)
                or window.feature_values != [window.features.get(name) for name in schema.names]
            ):
                skipped["schema_mismatch"] += 1
                continue
            if any(
                value is not None and not math.isfinite(value) for value in window.feature_values
            ):
                skipped["nonfinite_feature"] += 1
                continue
            if (
                window.quality["metrics"]["cpu_usage_percent"]["coverage_seconds"]
                < 0.8 * schema.window_seconds
            ):
                skipped["insufficient_feature_history"] += 1
                continue
            if any(
                metric["count"]
                and not (
                    window.window_start.timestamp()
                    < metric["first_timestamp"]
                    <= metric["last_timestamp"]
                    <= window.window_end.timestamp()
                )
                for metric in window.quality["metrics"].values()
            ):
                raise ValueError("Persisted feature quality contains future observations")
            label = label_window(
                window.host_id,
                window.window_end,
                horizon,
                settings.dataset_recovery_exclusion_seconds,
                events,
                probes.get(window.host_id, []),
                criteria,
                as_of,
                consecutive_breaches=3 if split_plan else 1,
            )
            input_health = (
                label_window(
                    window.host_id,
                    window.window_start,
                    schema.window_seconds,
                    0,
                    events,
                    probes.get(window.host_id, []),
                    criteria,
                    as_of,
                )
                if split_plan
                else None
            )
            candidate = {
                "host_id": window.host_id,
                "window_start": window.window_start.isoformat(),
                "window_end": window.window_end.isoformat(),
                "feature_values": window.feature_values,
                "label": label.value,
                "label_reason": label.reason,
                "failure_event_id": label.event_id,
                "horizon_seconds": horizon,
                "failure_type": next(
                    (e.failure_type for e in events if e.id == label.event_id), None
                ),
                "failure_onset": next(
                    (e.onset.isoformat() for e in events if e.id == label.event_id),
                    None,
                ),
            }
            if input_health and input_health.value == 0 and label.value != 1:
                healthy_inputs.append(
                    {
                        **candidate,
                        "input_health": "fully observed healthy input",
                        "input_health_criterion": criteria,
                    }
                )
            if label.value is None:
                skipped[label.reason] += 1
                continue
            rows.append(candidate)
        intervals = [
            (
                str(experiment.id),
                experiment.host_id,
                experiment.started_at,
                experiment.ended_at or as_of,
            )
            for experiment in experiments
        ]
        if split_plan:
            plan = json.loads(split_plan.read_text(encoding="utf-8"))
            rows, split = chronological_split(rows, intervals, horizon + 15, plan)
            healthy_inputs, _ = chronological_split(healthy_inputs, intervals, horizon + 15, plan)
            healthy_inputs = [row for row in healthy_inputs if row["split"] == "train"]
        else:
            rows, split = grouped_split(
                rows, intervals, horizon, settings.dataset_recovery_exclusion_seconds
            )
        payload = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows)
        healthy_payload = "".join(
            json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in healthy_inputs
        )
        identity_payload = (
            json.dumps(
                {
                    "schema_hash": schema.digest,
                    "horizon_seconds": horizon,
                    "recovery_exclusion_seconds": settings.dataset_recovery_exclusion_seconds,
                    "criterion": criteria,
                    "consecutive_breaches": 3,
                    "confirmation_tail_seconds": 15,
                    "split_plan": plan,
                },
                sort_keys=True,
            )
            if split_plan
            else None
        )
        digest_payload = (
            payload
            + "\nHEALTHY_TRAINING\n"
            + healthy_payload
            + "\nIDENTITY\n"
            + str(identity_payload)
            if split_plan
            else payload
        )
        digest = hashlib.sha256(digest_payload.encode()).hexdigest()
        target = output.resolve() / digest[:16]
        if not target.is_relative_to(ROOT):
            raise ValueError("Dataset output must stay inside the workspace")
        target.mkdir(parents=True, exist_ok=True)
        (target / "samples.jsonl").write_text(payload, encoding="utf-8", newline="\n")
        if split_plan:
            (target / "healthy_training.jsonl").write_text(
                healthy_payload, encoding="utf-8", newline="\n"
            )
        missing = {
            name: sum(row["feature_values"][index] is None for row in rows)
            for index, name in enumerate(schema.names)
        }
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "as_of": as_of.isoformat(),
            "dataset_sha256": digest,
            "samples": len(rows),
            "samples_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "healthy_training_sha256": hashlib.sha256(healthy_payload.encode()).hexdigest()
            if split_plan
            else None,
            "healthy_training_samples": len(healthy_inputs),
            "identity_payload": identity_payload,
            "dataset_digest_definition": "samples + healthy training + identity (with delimiters)"
            if split_plan
            else "samples",
            "isolation_forest_training_criterion": (
                "All probes across complete input window satisfy the SLO; "
                "no future-positive label. Isolated future degradation may be unlabeled. "
                "Full input+future interval stays in train."
            )
            if split_plan
            else None,
            "label_distribution": dict(Counter(str(row["label"]) for row in rows)),
            "failure_types": dict(Counter(event.failure_type for event in events)),
            "positive_failure_types": dict(
                Counter(
                    event.failure_type
                    for row in rows
                    for event in events
                    if row["label"] == 1 and row["failure_event_id"] == event.id
                )
            ),
            "hosts": sorted({row["host_id"] for row in rows}),
            "time_span": {
                "start": min((row["window_end"] for row in rows), default=None),
                "end": max((row["window_end"] for row in rows), default=None),
            },
            "horizon_seconds": horizon,
            "recovery_exclusion_seconds": settings.dataset_recovery_exclusion_seconds,
            "negative_label_criterion": criteria,
            "negative_evidence": (
                "Actual probe status and latency re-evaluated under this criterion"
            ),
            "max_probe_gap_seconds": 10,
            "failure_confirmation": {"consecutive_breaches": 3, "max_span_seconds": 15},
            "label_confirmation_tail_seconds": 15 if split_plan else 0,
            "negative_label_mode": "no sustained three-breach failure"
            if split_plan
            else "all probes healthy",
            "feature_schema": {
                "version": schema.version,
                "window_seconds": schema.window_seconds,
                "hash": schema.digest,
                "names": list(schema.names),
            },
            "missing_data_counts": missing,
            "missing_data_fraction": {
                name: count / len(rows) if rows else None for name, count in missing.items()
            },
            "excluded_windows": dict(skipped),
            "split": split,
            "split_counts": dict(Counter(row["split"] for row in rows)),
            "experiment_count": len(experiments),
            "observed_failure_event_count": len(events),
            "limitations": [
                "Probe-sampled future health, not continuous proof of availability",
                "Small local demo dataset; no predictive performance claim",
                "Missing values retained; no fitting or imputation",
            ],
        }
        report_file = target / "report.json"
        if report_file.exists():
            frozen = json.loads(report_file.read_text(encoding="utf-8"))
            if (
                frozen["dataset_sha256"] != digest
                or frozen.get("identity_payload") != identity_payload
            ):
                raise ValueError("Immutable dataset identity conflict")
            report = frozen
        else:
            report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (summary or ROOT / "docs" / "phase-4-dataset.json").write_text(
            json.dumps({"path": str(target), **report}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "path": str(target),
                    "samples": len(rows),
                    "label_distribution": report["label_distribution"],
                    "split": split,
                },
                indent=2,
            )
        )
        if not rows or not {0, 1}.issubset({row["label"] for row in rows}):
            raise SystemExit(
                "Dataset lacks observed positive/negative classes; collect more evidence"
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-seconds", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "datasets")
    parser.add_argument("--split-plan", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    asyncio.run(
        build(args.horizon_seconds, args.output, args.split_plan, args.summary),
        loop_factory=create_event_loop,
    )


if __name__ == "__main__":
    main()
