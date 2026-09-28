"""Two offline fits of frozen evidence; no registration or active-model change."""

import json
import math
import sys
from pathlib import Path
from uuid import uuid4

import torch

from training.train import train

ROOT = Path(__file__).resolve().parents[2]


def assert_historical_evaluation(actual, expected, path=()):
    """Require exact structure/counts and bounded floating arithmetic across platforms."""
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_historical_evaluation(actual[key], expected[key], (*path, key))
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for item, historical in zip(actual, expected, strict=True):
            assert_historical_evaluation(item, historical, path)
    elif isinstance(expected, float) and sys.platform != "win32" and "calibration" in path:
        # ARM64 calibration arithmetic differs from the frozen x86 report by
        # at most 3.1e-6; classifications stay exact.
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=5e-6), (actual, expected)
    elif isinstance(expected, float) and sys.platform != "win32" and "anomaly_score" in path:
        # IsolationForest reductions differ at the final floating-point bit
        # on Linux; the observed x86 mean drift is 1.2e-17.
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12), (actual, expected)
    else:
        assert actual == expected


def test_full_training_reproduces_weights_scores_and_threshold(monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("Git is deliberately unavailable in this exported-source fixture")

    monkeypatch.setattr("training.train.subprocess.run", no_git)
    metadata = json.loads(
        (ROOT / "artifacts/models/20260926T204848-mlp-1bcd8acd/metadata.json").read_text()
    )
    from training.artifacts import resolve_dataset

    dataset = resolve_dataset(metadata["dataset_reference"])
    historical_report = ROOT / "docs/phase-5-evaluation.json"
    historical_bytes = historical_report.read_bytes() if historical_report.exists() else None
    output = ROOT / ".runtime/phase9/reproducibility" / uuid4().hex
    first = train(dataset, output / "first", persist=False)
    second = train(dataset, output / "second", persist=False)
    assert (
        historical_report.read_bytes() if historical_report.exists() else None
    ) == historical_bytes
    one = torch.load(first / "mlp.pt", weights_only=True)
    two = torch.load(second / "mlp.pt", weights_only=True)
    assert one["input_width"] == two["input_width"] == 209
    for key in one["state_dict"]:
        assert torch.equal(one["state_dict"][key], two["state_dict"][key])
    for name in ["normalizer.json", "calibration.json", "history.json", "scores.jsonl"]:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    first_metadata = json.loads((first / "metadata.json").read_text())
    second_metadata = json.loads((second / "metadata.json").read_text())
    assert first_metadata["git_commit"] is second_metadata["git_commit"] is None
    assert first_metadata["threshold"] == second_metadata["threshold"]
    if sys.platform == "win32":
        assert first_metadata["threshold"] == metadata["threshold"]
    else:
        # ARM64 CPU arithmetic shifted the frozen x86 cutoff by 2.4e-7.
        assert math.isclose(
            first_metadata["threshold"], metadata["threshold"], rel_tol=0, abs_tol=1e-6
        )
    assert first_metadata["evaluation"] == second_metadata["evaluation"]
    assert_historical_evaluation(first_metadata["evaluation"], metadata["evaluation"])
