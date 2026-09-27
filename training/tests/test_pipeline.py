"""Contract fixtures only; reported performance always uses real dataset rows."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from training.labels import chronological_split
from training.runtime import fit_normalizer, normalize, probabilities
from training.train import (
    calibration_metrics,
    evaluate,
    load_dataset,
    load_healthy_training,
    select_threshold,
)


def test_training_only_imputation_and_scaling():
    train = np.array([[2.0, np.nan], [4.0, np.nan]])
    state = fit_normalizer(train)
    assert state["medians"] == [3.0, 0.0]
    assert state["all_missing_columns"] == [1]
    assert normalize(np.array([[100.0, np.nan]]), state).tolist() == [[97.0, 0.0]]
    assert state["means"] == [3.0, 0.0]


def test_calibration_bins_include_endpoints():
    metrics = calibration_metrics(np.array([0, 1, 0]), np.array([0.0, 1.0, 0.3]))
    assert sum(b["count"] for b in metrics["bins"]) == 3
    assert metrics["brier_score"] == pytest.approx(0.03)


def test_threshold_chosen_from_supplied_validation_probabilities():
    assert select_threshold(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.7, 0.8])) == 0.7


def test_probabilities_are_finite_with_extreme_logits():
    values = probabilities(np.array([-1e9, 0.0, 1e9]), {"slope": 1.0, "intercept": 0.0})
    assert np.isfinite(values).all()
    assert values[1] == 0.5


def test_chronological_purge_includes_input_and_label_horizon():
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    plan = {
        "train_boundary": (origin + timedelta(minutes=30)).isoformat(),
        "test_boundary": (origin + timedelta(minutes=70)).isoformat(),
        "declared_at": origin.isoformat(),
    }
    rows = [
        {
            "host_id": "fixture",
            "window_start": (origin + timedelta(minutes=t - 15)).isoformat(),
            "window_end": (origin + timedelta(minutes=t)).isoformat(),
        }
        for t in (10, 20, 30, 46, 60, 86)
    ]
    assigned, report = chronological_split(rows, [], 600, plan)
    assert [r["split"] for r in assigned] == ["train", "validation", "test"]
    assert report["purged_samples"] == 3


def test_metrics_exposure_and_useful_warning_lead():
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "host_id": "fixture",
            "label": label,
            "failure_event_id": "event" if label else None,
            "failure_type": "http_error" if label else None,
            "failure_onset": (origin + timedelta(minutes=10)).isoformat() if label else None,
            "window_end": (origin + timedelta(minutes=i)).isoformat(),
        }
        for i, label in enumerate((0, 0, 1, 1))
    ]
    result = evaluate(rows, np.array([0.9, 0.1, 0.8, 0.2]), 0.5, np.zeros(4))
    assert result["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    assert result["false_alerts_per_non_failure_host_hour"] == 30
    assert result["lead_time_by_event"][0]["lead_seconds"] == 480


def test_training_refuses_missing_held_out_evidence(tmp_path):
    payload = b'{"label": 0, "split": "train"}\n'
    (tmp_path / "samples.jsonl").write_bytes(payload)
    report = {
        "dataset_sha256": hashlib.sha256(payload).hexdigest(),
        "split": {"status": "awaiting_held_out_evidence"},
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Independent train/validation/test"):
        load_dataset(tmp_path)


def test_training_refuses_mutated_dataset(tmp_path):
    (tmp_path / "samples.jsonl").write_bytes(b"mutated")
    (tmp_path / "report.json").write_text(json.dumps({"dataset_sha256": "incorrect"}))
    with pytest.raises(ValueError, match="digest mismatch"):
        load_dataset(tmp_path)


def test_training_refuses_shared_experiment_across_hosts(tmp_path):
    from app.features.schema import FeatureSchema

    schema = FeatureSchema(window_seconds=900)
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for index, partition in enumerate(("train", "validation", "test")):
        for label in (0, 1):
            end = origin + timedelta(minutes=index * 120 + 15 + label)
            rows.append(
                {
                    "host_id": "fixture-" + partition,
                    "window_start": (end - timedelta(minutes=15)).isoformat(),
                    "window_end": end.isoformat(),
                    "horizon_seconds": 600,
                    "label": label,
                    "split": partition,
                    "feature_values": [None] * len(schema.names),
                    "experiment_ids": ["shared-fixture"] if index < 2 and label else [],
                }
            )
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (tmp_path / "samples.jsonl").write_bytes(payload)
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "dataset_sha256": hashlib.sha256(payload).hexdigest(),
                "split": {"status": "available"},
                "feature_schema": {
                    "window_seconds": 900,
                    "version": schema.version,
                    "hash": schema.digest,
                    "names": list(schema.names),
                },
                "horizon_seconds": 600,
                "label_confirmation_tail_seconds": 15,
            }
        )
    )
    with pytest.raises(ValueError, match="Experiment leakage"):
        load_dataset(tmp_path)


def test_healthy_inputs_can_have_unknown_future_labels(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    criterion = {"latency_slo_seconds": 0.5}
    healthy = [
        {
            "host_id": "fixture",
            "window_start": (start + timedelta(minutes=i)).isoformat(),
            "window_end": (start + timedelta(minutes=i + 15)).isoformat(),
            "horizon_seconds": 600,
            "feature_values": [1.0],
            "label": None,
            "split": "train",
            "input_health": "fully observed healthy input",
            "input_health_criterion": criterion,
        }
        for i in range(5)
    ]
    payload = "".join(json.dumps(r) + "\n" for r in healthy).encode()
    (tmp_path / "samples.jsonl").write_bytes(b"")
    (tmp_path / "healthy_training.jsonl").write_bytes(payload)
    report = {
        "healthy_training_sha256": hashlib.sha256(payload).hexdigest(),
        "dataset_sha256": hashlib.sha256(b"\nHEALTHY_TRAINING\n" + payload).hexdigest(),
        "healthy_training_samples": 5,
        "feature_schema": {"names": ["cpu"]},
        "negative_label_criterion": criterion,
    }
    assert len(load_healthy_training(tmp_path, report, [])) == 5
    healthy[0]["label"] = 1
    payload = "".join(json.dumps(r) + "\n" for r in healthy).encode()
    (tmp_path / "healthy_training.jsonl").write_bytes(payload)
    report["healthy_training_sha256"] = hashlib.sha256(payload).hexdigest()
    report["dataset_sha256"] = hashlib.sha256(b"\nHEALTHY_TRAINING\n" + payload).hexdigest()
    with pytest.raises(ValueError, match="Invalid healthy training input"):
        load_healthy_training(tmp_path, report, [])


def test_sustained_definition_accepts_isolated_spikes_but_excludes_bursts():
    from training.labels import Probe, label_window

    start = datetime(2026, 1, 1, tzinfo=UTC)
    criterion = {"latency_slo_seconds": 0.5}
    probes = [
        Probe(start + timedelta(seconds=i), i not in {4, 16}, criterion) for i in range(0, 28, 2)
    ]
    result = label_window(
        "fixture",
        start,
        10,
        0,
        [],
        probes,
        criterion,
        start + timedelta(seconds=28),
        consecutive_breaches=3,
    )
    assert result.value == 0
    assert result.reason == "observed_no_sustained_failure_horizon"
    burst = [
        Probe(start + timedelta(seconds=i), i not in {4, 6, 8}, criterion) for i in range(0, 28, 2)
    ]
    assert (
        label_window(
            "fixture",
            start,
            10,
            0,
            [],
            burst,
            criterion,
            start + timedelta(seconds=28),
            consecutive_breaches=3,
        ).value
        is None
    )


def test_sustained_negative_requires_confirmation_tail():
    from training.labels import Probe, label_window

    start = datetime(2026, 1, 1, tzinfo=UTC)
    criterion = {"latency_slo_seconds": 0.5}
    probes = [
        Probe(start + timedelta(seconds=i), i not in {8, 10}, criterion) for i in range(0, 12, 2)
    ]
    result = label_window(
        "fixture",
        start,
        10,
        0,
        [],
        probes,
        criterion,
        start + timedelta(seconds=11),
        consecutive_breaches=3,
    )
    assert result.value is None
    assert result.reason == "right_censored_confirmation"
