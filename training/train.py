"""Train on actual persisted evidence; select on validation; evaluate test once."""

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import random
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import torch
from numpy.typing import NDArray
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sqlalchemy.ext.asyncio import async_sessionmaker
from torch import nn

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.features.schema import FeatureSchema
from app.models import EvaluationRun, ModelVersion
from training.runtime import FailureMLP, fit_normalizer, normalize, probabilities

ROOT = Path(__file__).resolve().parents[1]
SEED = 1729


def calibration_metrics(y: NDArray[Any], p: NDArray[Any]) -> dict[str, Any]:
    bins = []
    ece = 0.0
    indices = np.minimum((p * 10).astype(int), 9)
    for index in range(10):
        lower = index / 10
        selected = indices == index
        count = int(selected.sum())
        if count:
            mean, rate = float(p[selected].mean()), float(y[selected].mean())
            ece += count / len(y) * abs(mean - rate)
            bins.append(
                {
                    "lower": float(lower),
                    "count": count,
                    "mean_probability": mean,
                    "observed_rate": rate,
                }
            )
    return {
        "brier_score": float(brier_score_loss(y, p)),
        "ece_10_bins": ece,
        "bins": bins,
    }


def select_threshold(y: NDArray[Any], p: NDArray[Any]) -> float:
    candidates = sorted(set([0.0, 0.5, 1.0, *p.tolist()]))
    return float(max(candidates, key=lambda t: (f1_score(y, p >= t, zero_division=0), t)))


def evaluate(
    rows: list[dict[str, Any]], p: NDArray[Any], threshold: float, anomaly: NDArray[Any]
) -> dict[str, Any]:
    y = np.array([row["label"] for row in rows])
    warnings = p >= threshold
    tn, fp, fn, tp = confusion_matrix(y, warnings, labels=[0, 1]).ravel()
    # Each eligible minute contributes one minute of measured scoring exposure.
    healthy_hours = int((y == 0).sum()) / 60
    epochs = 0
    last: dict[str, datetime] = {}
    for row, warning in zip(rows, warnings, strict=True):
        if not warning:
            continue
        at = datetime.fromisoformat(row["window_end"])
        host = row["host_id"]
        new_epoch = host not in last or at - last[host] >= timedelta(minutes=5)
        if new_epoch and row["label"] == 0:
            epochs += 1
        if new_epoch:
            last[host] = at
    leads = []
    for event_id in sorted({row["failure_event_id"] for row in rows if row["label"] == 1}):
        indices = [i for i, row in enumerate(rows) if row["failure_event_id"] == event_id]
        useful = [
            i
            for i in indices
            if warnings[i]
            and (
                datetime.fromisoformat(rows[i]["failure_onset"])
                - datetime.fromisoformat(rows[i]["window_end"])
            ).total_seconds()
            >= 60
        ]
        first = min(useful, key=lambda i: rows[i]["window_end"]) if useful else None
        lead = (
            (
                datetime.fromisoformat(rows[first]["failure_onset"])
                - datetime.fromisoformat(rows[first]["window_end"])
            ).total_seconds()
            if first is not None
            else None
        )
        leads.append(
            {
                "event_id": event_id,
                "failure_type": rows[indices[0]]["failure_type"],
                "earliest_useful_warning": rows[first]["window_end"] if first is not None else None,
                "lead_seconds": lead,
                "eligible_pre_failure_windows": len(indices),
            }
        )
    types = {}
    for kind in sorted({row["failure_type"] for row in rows if row["label"] == 1}):
        positive = np.array([row["label"] == 1 and row["failure_type"] == kind for row in rows])
        mask = (y == 0) | positive
        type_truth = positive[mask].astype(int)
        type_warnings = warnings[mask]
        type_tn, type_fp, type_fn, type_tp = confusion_matrix(
            type_truth, type_warnings, labels=[0, 1]
        ).ravel()
        types[kind] = {
            "f1": float(f1_score(type_truth, type_warnings, zero_division=0)),
            "pr_auc": float(average_precision_score(type_truth, p[mask])),
            "roc_auc": float(roc_auc_score(type_truth, p[mask])),
            "confusion_matrix": {
                "tn": int(type_tn),
                "fp": int(type_fp),
                "fn": int(type_fn),
                "tp": int(type_tp),
            },
            "lead_time_by_event": [event for event in leads if event["failure_type"] == kind],
            "positive_windows": int(positive.sum()),
            "true_positive_windows": int((positive & warnings).sum()),
            "recall": float(warnings[positive].mean()),
            "precision_against_shared_healthy_baseline": float(
                (positive & warnings).sum() / max(1, int((positive & warnings).sum()) + int(fp))
            ),
        }
    return {
        "samples": len(rows),
        "label_distribution": dict(Counter(str(v) for v in y)),
        "precision": float(precision_score(y, warnings, zero_division=0)),
        "recall": float(recall_score(y, warnings, zero_division=0)),
        "f1": float(f1_score(y, warnings, zero_division=0)),
        "pr_auc": float(average_precision_score(y, p)),
        "pr_auc_definition": "average precision (step integral)",
        "roc_auc": float(roc_auc_score(y, p)),
        "classification_baselines": {
            "always_warn_precision": float(y.mean()),
            "always_warn_recall": 1.0,
            "always_warn_f1": float(f1_score(y, np.ones_like(y), zero_division=0)),
            "never_warn_f1": 0.0,
            "random_ranking_pr_auc": float(y.mean()),
        },
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
        "non_failure_scoring_host_hours": healthy_hours,
        "false_warning_windows_per_non_failure_host_hour": int(fp) / healthy_hours,
        "false_alert_epochs": epochs,
        "false_alerts_per_non_failure_host_hour": epochs / healthy_hours,
        "false_alerts_per_non_failure_host_day": epochs / healthy_hours * 24,
        "alert_epoch_policy": "5-minute host cooldown; offline eligible windows only",
        "exposure_policy": "one minute per eligible negative row; gaps excluded",
        "useful_warning_minimum_lead_seconds": 60,
        "lead_time_by_event": leads,
        "per_failure_type": types,
        "calibration": calibration_metrics(y, p),
        "anomaly_score": {
            "definition": "negative IsolationForest decision_function; larger is unfamiliar",
            "mean": float(anomaly.mean()),
            "min": float(anomaly.min()),
            "max": float(anomaly.max()),
            "combined_with_failure_probability": False,
        },
    }


def load_dataset(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report = json.loads((path / "report.json").read_text(encoding="utf-8"))
    payload = (path / "samples.jsonl").read_bytes()
    if hashlib.sha256(payload).hexdigest() != report.get(
        "samples_sha256", report["dataset_sha256"]
    ):
        raise ValueError("Dataset digest mismatch")
    rows = [json.loads(line) for line in payload.splitlines()]
    if report["split"]["status"] != "available":
        raise ValueError("Independent train/validation/test evidence is required")
    schema = FeatureSchema(window_seconds=report["feature_schema"]["window_seconds"])
    if (
        report["feature_schema"]["names"] != list(schema.names)
        or report["feature_schema"]["hash"] != schema.digest
        or report["feature_schema"]["version"] != schema.version
    ):
        raise ValueError("Feature schema contract mismatch")
    width = len(report["feature_schema"]["names"])
    identities = {(r["host_id"], r["window_end"]) for r in rows}
    if len(identities) != len(rows):
        raise ValueError("Duplicate scoring windows")
    if any(
        r["split"] not in {"train", "validation", "test"}
        or r["label"] not in {0, 1}
        or r["horizon_seconds"] != report["horizon_seconds"]
        for r in rows
    ):
        raise ValueError("Invalid partition, label or horizon")
    for split in ("train", "validation", "test"):
        selected = [r for r in rows if r["split"] == split]
        if {r["label"] for r in selected} != {0, 1}:
            raise ValueError(f"{split} must contain real healthy and failure labels")
    for row in rows:
        if len(row["feature_values"]) != width:
            raise ValueError("Feature vector width mismatch")
        if any(v is not None and not np.isfinite(v) for v in row["feature_values"]):
            raise ValueError("Non-finite feature value")
    for a in rows:
        for b in rows:
            if a["split"] == b["split"]:
                continue
            if set(a["experiment_ids"]) & set(b["experiment_ids"]):
                raise ValueError("Experiment leakage")
            if a["host_id"] != b["host_id"]:
                continue
            if datetime.fromisoformat(a["window_start"]) <= datetime.fromisoformat(
                b["window_end"]
            ) + timedelta(
                seconds=b["horizon_seconds"] + report["label_confirmation_tail_seconds"]
            ) and datetime.fromisoformat(b["window_start"]) <= datetime.fromisoformat(
                a["window_end"]
            ) + timedelta(seconds=a["horizon_seconds"] + report["label_confirmation_tail_seconds"]):
                raise ValueError("Input or label interval leakage between partitions")
    return rows, report


def load_healthy_training(
    dataset: Path, report: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    payload = (dataset / "healthy_training.jsonl").read_bytes()
    if hashlib.sha256(payload).hexdigest() != report["healthy_training_sha256"]:
        raise ValueError("Healthy training digest mismatch")
    combined = (dataset / "samples.jsonl").read_bytes() + b"\nHEALTHY_TRAINING\n" + payload
    if report.get("identity_payload") is not None:
        combined += b"\nIDENTITY\n" + report["identity_payload"].encode()
    if hashlib.sha256(combined).hexdigest() != report["dataset_sha256"]:
        raise ValueError("Composite dataset digest mismatch")
    healthy = [json.loads(line) for line in payload.splitlines()]
    if len({(r["host_id"], r["window_end"]) for r in healthy}) != len(healthy):
        raise ValueError("Duplicate healthy training windows")
    if len(healthy) != report["healthy_training_samples"]:
        raise ValueError("Healthy training count mismatch")
    if len(healthy) < 5:
        raise ValueError("Isolation Forest needs five actually healthy training input windows")
    for row in healthy:
        if any(v is not None and not np.isfinite(v) for v in row["feature_values"]):
            raise ValueError("Non-finite healthy training feature")
        if row["input_health_criterion"] != report["negative_label_criterion"]:
            raise ValueError("Healthy input criterion mismatch")
        if (
            row["split"] != "train"
            or row["label"] not in {0, None}
            or row["input_health"] != "fully observed healthy input"
        ):
            raise ValueError("Invalid healthy training input")
        if len(row["feature_values"]) != len(report["feature_schema"]["names"]):
            raise ValueError("Healthy input schema mismatch")
        for other in rows:
            if other["split"] == "train" or other["host_id"] != row["host_id"]:
                continue
            if datetime.fromisoformat(row["window_end"]) + timedelta(
                seconds=row["horizon_seconds"] + report["label_confirmation_tail_seconds"]
            ) >= datetime.fromisoformat(other["window_start"]):
                raise ValueError("Healthy training overlaps a held-out interval")
    return healthy


async def register(target: Path, metadata: dict[str, Any]) -> None:
    settings = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            model = ModelVersion(
                version=metadata["version"],
                schema_version=metadata["feature_schema"]["version"],
                schema_hash=metadata["feature_schema"]["hash"],
                artifact_uri=str(target),
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
                    completed_at=datetime.now(UTC),
                )
            )
    finally:
        await engine.dispose()


def train(dataset: Path, output: Path, persist: bool = True) -> Path:
    rows, report = load_dataset(dataset)
    healthy_rows = load_healthy_training(dataset, report, rows)
    now = datetime.now(UTC)
    version = f"{now:%Y%m%dT%H%M%S}-mlp-{report['dataset_sha256'][:8]}"
    target = output.resolve() / version
    if not target.is_relative_to(ROOT):
        raise ValueError("Model output must remain in the workspace")
    target.mkdir(parents=True, exist_ok=False)
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    partitions = {
        name: [r for r in rows if r["split"] == name] for name in ("train", "validation", "test")
    }
    arrays = {
        name: np.array([r["feature_values"] for r in values], dtype=float)
        for name, values in partitions.items()
    }
    labels = {
        name: np.array([r["label"] for r in values], dtype=np.float32)
        for name, values in partitions.items()
    }
    normalizer = fit_normalizer(arrays["train"])
    inputs = {
        name: torch.from_numpy(normalize(array, normalizer)) for name, array in arrays.items()
    }
    model = FailureMLP(inputs["train"].shape[1])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01)
    positive_weight = float((labels["train"] == 0).sum() / (labels["train"] == 1).sum())
    loss_function = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(positive_weight))
    validation_loss_function = nn.BCEWithLogitsLoss()
    best_loss, best_epoch, stale = float("inf"), 0, 0
    best_state = copy.deepcopy(model.state_dict())
    history = []
    for epoch in range(1, 501):
        model.train()
        optimizer.zero_grad()
        loss = loss_function(model(inputs["train"]), torch.from_numpy(labels["train"]))
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(
                validation_loss_function(
                    model(inputs["validation"]), torch.from_numpy(labels["validation"])
                )
            )
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(loss.detach()),
                "validation_loss": val_loss,
            }
        )
        if val_loss < best_loss - 1e-5:
            best_loss, best_epoch, stale = val_loss, epoch, 0
            best_state = copy.deepcopy(model.state_dict())
            torch.save(
                {"state_dict": best_state, "epoch": epoch, "validation_loss": val_loss},
                target / "best_checkpoint.pt",
            )
        else:
            stale += 1
        if stale >= 30:
            break
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        val_logits = model(inputs["validation"]).numpy()
    calibration: dict[str, Any] = {
        "method": "identity",
        "slope": 1.0,
        "intercept": 0.0,
        "fit_partition": "validation",
    }
    raw = probabilities(val_logits, calibration)
    before = calibration_metrics(labels["validation"], raw)
    baseline_brier = float(np.mean((labels["validation"] - labels["train"].mean()) ** 2))
    poor = before["ece_10_bins"] > 0.1 or before["brier_score"] > baseline_brier
    if poor:
        platt = LogisticRegression(C=1.0, random_state=SEED).fit(
            val_logits.reshape(-1, 1), labels["validation"]
        )
        calibration.update(
            method="regularized Platt scaling",
            slope=float(platt.coef_[0, 0]),
            intercept=float(platt.intercept_[0]),
        )
    val_probabilities = probabilities(val_logits, calibration)
    threshold = select_threshold(labels["validation"], val_probabilities)
    healthy = normalize(
        np.array([r["feature_values"] for r in healthy_rows], dtype=float), normalizer
    )
    if len(healthy) < 5:
        raise ValueError("Isolation Forest needs at least five healthy training windows")
    forest = IsolationForest(
        n_estimators=200, contamination="auto", random_state=SEED, n_jobs=1
    ).fit(healthy)
    # Model, calibration and threshold are frozen before test inference.
    evaluation, scored = {}, []
    for name in ("validation", "test"):
        with torch.no_grad():
            logits = model(inputs[name]).numpy()
        p = probabilities(logits, calibration)
        anomaly = -forest.decision_function(inputs[name].numpy())
        evaluation[name] = evaluate(partitions[name], p, threshold, anomaly)
        for row, probability, score in zip(partitions[name], p, anomaly, strict=True):
            scored.append(
                {
                    **{k: v for k, v in row.items() if k != "feature_values"},
                    "failure_probability": float(probability),
                    "anomaly_score": float(score),
                    "warning": bool(probability >= threshold),
                }
            )
    torch.save(
        {
            "state_dict": best_state,
            "input_width": inputs["train"].shape[1],
            "architecture": "209-16-8-1 ReLU dropout0.1",
        },
        target / "mlp.pt",
    )
    joblib.dump(forest, target / "isolation_forest.joblib")
    (target / "normalizer.json").write_text(json.dumps(normalizer, indent=2), encoding="utf-8")
    (target / "calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    (target / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    (target / "scores.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in scored), encoding="utf-8", newline="\n"
    )
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        git_commit = commit.stdout.strip() if commit.returncode == 0 else None
    except FileNotFoundError:
        git_commit = None  # Exported/container sources may not contain a Git executable.
    metadata = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "dataset_reference": str(dataset.resolve()),
        "dataset_sha256": report["dataset_sha256"],
        "dataset_report_sha256": hashlib.sha256((dataset / "report.json").read_bytes()).hexdigest(),
        "feature_schema": report["feature_schema"],
        "horizon_seconds": report["horizon_seconds"],
        "failure_confirmation": report["failure_confirmation"],
        "label_confirmation_tail_seconds": report["label_confirmation_tail_seconds"],
        "negative_label_mode": report["negative_label_mode"],
        "split": report["split"],
        "split_counts": {k: len(v) for k, v in partitions.items()},
        "positive_failure_types_by_split": {
            name: dict(Counter(row["failure_type"] for row in values if row["label"] == 1))
            for name, values in partitions.items()
        },
        "threshold": threshold,
        "threshold_selection": "maximize validation F1; ties choose larger threshold",
        "normalization_fit_partition": "train",
        "positive_class_weight": positive_weight,
        "training": {
            "epochs": epoch,
            "best_epoch": best_epoch,
            "patience": 30,
            "best_validation_loss": best_loss,
            "optimizer": "AdamW lr0.001 weight_decay0.01",
        },
        "isolation_forest": {
            "fit_partition": "fully observed healthy training input windows",
            "criterion": report["isolation_forest_training_criterion"],
            "input_sha256": report["healthy_training_sha256"],
            "samples": len(healthy),
            "estimators": 200,
            "contamination": "auto",
        },
        "calibration": {
            **calibration,
            "trigger": "validation ECE > 0.1 or Brier worse than training prevalence",
            "poor_calibration_detected": poor,
            "validation_before": before,
            "validation_after": calibration_metrics(labels["validation"], val_probabilities),
        },
        "evaluation": evaluation,
        "git_commit": git_commit,
        "git_working_tree": "uncommitted project; commit alone does not identify this source",
        "dependencies": {
            p: importlib.metadata.version(p) for p in ("torch", "scikit-learn", "numpy", "joblib")
        },
        "limitations": [
            "Small single-host local chaos dataset; correlated minute windows",
            (
                "Validation reused for early stopping, calibration and threshold; "
                "validation metrics optimistic"
            ),
            ("Abrupt injections have no guaranteed precursor; warnings may be coincidental"),
            "Only events with eligible pre-failure windows enter lead-time metrics",
            (
                "False-alert rates extrapolate short non-failure exposure; "
                "no continuous deployment estimate"
            ),
            "Unfamiliarity score is separate from supervised failure probability",
        ],
    }
    metadata["evaluated_failure_types"] = sorted(evaluation["test"]["per_failure_type"])
    metadata["observed_failure_types"] = report["failure_types"]
    metadata["test_calibration_warning"] = evaluation["test"]["calibration"]["ece_10_bins"] > 0.1
    metadata["test_calibration_policy"] = (
        "Held-out calibration is reported without test adaptation; "
        "recalibration requires new validation data"
    )
    metadata["source_sha256"] = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            ROOT / "training" / name
            for name in (
                "train.py",
                "runtime.py",
                "labels.py",
                "build_dataset.py",
                "uv.lock",
            )
        ]
    }
    metadata["artifact_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
    )
    if persist:
        asyncio.run(register(target, metadata), loop_factory=create_event_loop)
        (ROOT / "docs" / "phase-5-evaluation.json").write_text(
            json.dumps({"path": str(target), **metadata}, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    print(json.dumps({"path": str(target), "test": evaluation["test"]}, indent=2))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "models")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()
    train(args.dataset, args.output, not args.no_register)


if __name__ == "__main__":
    main()
