"""Resolve a frozen dataset identity under the current trusted artifact root."""

import re
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]


def resolve_dataset(reference: str) -> Path:
    # Preserve original provenance in metadata; relocation never rewrites it.
    parts = PureWindowsPath(reference).parts if "\\" in reference else Path(reference).parts
    if (
        len(parts) < 3
        or parts[-3:-1] != ("artifacts", "datasets")
        or not re.fullmatch(r"[0-9a-f]{16}", parts[-1])
    ):
        raise ValueError("Untrusted background dataset identity")
    root = (ROOT / "artifacts" / "datasets").resolve()
    dataset = (root / parts[-1]).resolve()
    if dataset.parent != root:
        raise ValueError("Untrusted background dataset location")
    return dataset
