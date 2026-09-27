"""Verified artifact loading, separate scores and bounded permutation SHAP."""

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import shap
import torch
from numpy.typing import NDArray

from training.artifacts import resolve_dataset
from training.runtime import FailureMLP, normalize, probabilities
from training.train import load_dataset, load_healthy_training

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARTIFACTS = {
    "mlp.pt",
    "normalizer.json",
    "calibration.json",
    "isolation_forest.joblib",
}


def validate_artifacts(path: Path, metadata: dict[str, Any]) -> None:
    manifest = metadata.get("artifact_sha256", {})
    if not isinstance(manifest, dict) or not REQUIRED_ARTIFACTS.issubset(manifest):
        raise ValueError("Incomplete model artifact manifest")
    for name, digest in manifest.items():
        artifact = (path / name).resolve()
        if (
            Path(name).name != name
            or artifact.parent != path
            or artifact.stat().st_size > 64 * 1024 * 1024
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Model artifact digest or location mismatch")
    schema = metadata["feature_schema"]
    names = schema["names"]
    if (
        not isinstance(names, list)
        or not names
        or len(names) > 4096
        or any(not isinstance(name, str) for name in names)
        or len(set(names)) != len(names)
        or not np.isfinite(metadata["threshold"])
        or not 0 <= metadata["threshold"] <= 1
        or not 30 <= metadata["horizon_seconds"] <= 3600
    ):
        raise ValueError("Invalid model contract")


class Scorer:
    def __init__(self, path: Path):
        path = path.resolve()
        if not path.is_relative_to(ROOT / "artifacts" / "models"):
            raise ValueError("Only trusted workspace model artifacts may be loaded")
        metadata_file = (path / "metadata.json").resolve()
        if metadata_file.parent != path or metadata_file.stat().st_size > 262144:
            raise ValueError("Invalid metadata location or size")
        self.metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        validate_artifacts(path, self.metadata)
        dataset = resolve_dataset(self.metadata["dataset_reference"])
        self.dataset_path = dataset
        if (
            hashlib.sha256((dataset / "report.json").read_bytes()).hexdigest()
            != self.metadata["dataset_report_sha256"]
        ):
            raise ValueError("Frozen report digest mismatch")
        rows, report = load_dataset(dataset)
        load_healthy_training(dataset, report, rows)
        if report["dataset_sha256"] != self.metadata["dataset_sha256"]:
            raise ValueError("Model dataset mismatch")
        if report["feature_schema"] != self.metadata["feature_schema"]:
            raise ValueError("Model feature schema differs from frozen dataset")
        self.names = self.metadata["feature_schema"]["names"]
        self.normalizer = json.loads((path / "normalizer.json").read_text())
        self.calibration = json.loads((path / "calibration.json").read_text())
        for key in ("medians", "means", "scales"):
            vector = np.asarray(self.normalizer[key], dtype=float)
            if vector.shape != (len(self.names),) or not np.isfinite(vector).all():
                raise ValueError("Incompatible model normalizer")
        if (
            any(scale <= 0 for scale in self.normalizer["scales"])
            or self.normalizer["fit_partition"] != "train"
            or self.calibration["fit_partition"] != "validation"
            or not np.isfinite([self.calibration["slope"], self.calibration["intercept"]]).all()
        ):
            raise ValueError("Invalid preprocessing or calibration contract")
        torch.set_num_threads(1)
        saved = torch.load(path / "mlp.pt", map_location="cpu", weights_only=True)
        if saved["input_width"] != len(self.names):
            raise ValueError("Incompatible model input width")
        if not all(torch.isfinite(value).all() for value in saved["state_dict"].values()):
            raise ValueError("Non-finite model weights")
        self.model = FailureMLP(saved["input_width"])
        self.model.load_state_dict(saved["state_dict"])
        self.model.eval()
        self.forest = joblib.load(path / "isolation_forest.joblib")
        if self.forest.n_features_in_ != len(self.names):
            raise ValueError("Incompatible anomaly model input width")
        self.background = normalize(
            np.array(
                [r["feature_values"] for r in rows if r["split"] == "train"],
                dtype=float,
            ),
            self.normalizer,
        )
        self.explainer: Any = None

    def probability(self, values: NDArray[Any]) -> NDArray[Any]:
        with torch.no_grad():
            return probabilities(
                self.model(torch.from_numpy(values.astype(np.float32))).numpy(),
                self.calibration,
            )

    def score(self, values: list[float | None], explain: bool = True) -> dict[str, Any]:
        if len(values) != len(self.names) or any(
            v is not None and not np.isfinite(v) for v in values
        ):
            raise ValueError("Invalid feature vector")
        normalized = normalize(np.array([values], dtype=float), self.normalizer)
        probability = float(self.probability(normalized)[0])
        anomaly = float(-self.forest.decision_function(normalized)[0])
        if not np.isfinite([probability, anomaly]).all() or not 0 <= probability <= 1:
            raise ValueError("Model returned invalid scores")
        result: dict[str, Any] = {
            "failure_probability": probability,
            "anomaly_score": anomaly,
            "top_drivers": [],
            "shap": {"status": "below_threshold"},
        }
        if explain and probability >= self.metadata["threshold"]:
            if self.explainer is None:
                self.explainer = shap.PermutationExplainer(
                    self.probability,
                    self.background,
                    feature_names=self.names,
                    seed=1729,
                )
            budget = 4 * (2 * len(self.names) + 1)
            explanation = self.explainer(normalized, max_evals=budget, silent=True)
            contributions = np.asarray(explanation.values[0], dtype=float)
            base = float(np.asarray(explanation.base_values).reshape(-1)[0])
            residual = probability - base - float(contributions.sum())
            if not np.isfinite(contributions).all() or abs(residual) > 1e-5:
                raise ValueError("SHAP additivity check failed")
            result["top_drivers"] = [
                {
                    "feature": self.names[i],
                    "contribution": float(contributions[i]),
                    "value": values[i],
                }
                for i in np.argsort(-np.abs(contributions))[:10]
            ]
            result["shap"] = {
                "status": "complete",
                "method": "permutation SHAP",
                "max_evals": budget,
                "background_partition": "train",
                "background_rows": len(self.background),
                "base_probability": base,
                "contribution_sum": float(contributions.sum()),
                "additivity_residual": residual,
                "units": "calibrated failure probability",
                "limitations": (
                    "Approximate marginal associations with correlated inputs; not causation"
                ),
            }
        return result
