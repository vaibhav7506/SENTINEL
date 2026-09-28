"""Create a narrow Docker context without private data or inaccessible runtime caches."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".runtime/phase3/build-context"
TARGET.mkdir(parents=True, exist_ok=True)
for name in (
    "backend/app",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/saas-runtime/requirements.txt",
    "queue",
    "inference/linux/pyproject.toml",
    "inference/linux/uv.lock",
    "artifacts/models/20260926T204848-mlp-1bcd8acd",
    "artifacts/datasets/1bcd8acdc1fab6f9",
):
    source, destination = ROOT / name, TARGET / name
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"),
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
for folder in ("inference", "training"):
    destination = TARGET / folder
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / folder).glob("*.py"):
        shutil.copy2(source, destination / source.name)
print(TARGET)
