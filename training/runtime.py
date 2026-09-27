"""Offline inference contracts shared with the subsequent inference phase."""

from typing import Any, cast

import numpy as np
import torch
from numpy.typing import NDArray
from torch import nn


class FailureMLP(nn.Module):
    def __init__(self, width: int):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(width, 16),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(16, 8),
            nn.ReLU(),
            nn.Linear(8, 1),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return cast(torch.Tensor, self.layers(values).squeeze(-1))


def fit_normalizer(values: NDArray[Any]) -> dict[str, Any]:
    medians = np.array(
        [
            np.median(column[np.isfinite(column)]) if np.isfinite(column).any() else 0.0
            for column in values.T
        ]
    )
    filled = np.where(np.isfinite(values), values, medians)
    means = filled.mean(axis=0)
    scales = filled.std(axis=0)
    scales[scales < 1e-8] = 1.0
    return {
        "medians": medians.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "all_missing_columns": np.flatnonzero(~np.isfinite(values).any(axis=0)).tolist(),
        "fit_partition": "train",
        "policy": "training median; all-missing columns zero",
    }


def normalize(values: NDArray[Any], state: dict[str, Any]) -> NDArray[Any]:
    filled = np.where(np.isfinite(values), values, np.asarray(state["medians"]))
    return ((filled - np.asarray(state["means"])) / np.asarray(state["scales"])).astype(np.float32)


def probabilities(logits: NDArray[Any], calibration: dict[str, Any]) -> NDArray[Any]:
    adjusted = logits * calibration["slope"] + calibration["intercept"]
    return cast(NDArray[Any], 1.0 / (1.0 + np.exp(-np.clip(adjusted, -50, 50))))
