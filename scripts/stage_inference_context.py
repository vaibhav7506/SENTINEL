"""Copy an explicit source/artifact allowlist into a private local Docker build context."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    target = ROOT / ".runtime/phase10/build-context"
    files = [
        ROOT / ".dockerignore",
        ROOT / "inference/Dockerfile",
        ROOT / "backend/pyproject.toml",
        ROOT / "backend/uv.lock",
        ROOT / "backend/alembic.ini",
        ROOT / "inference/linux/pyproject.toml",
        ROOT / "inference/linux/uv.lock",
        ROOT / "training/pyproject.toml",
        ROOT / "training/uv.lock",
        ROOT / "inference/pyproject.toml",
        ROOT / "scripts/check_phase10_database.py",
    ]
    for directory in (
        "backend/app",
        "backend/alembic",
        "backend/tests",
        "inference/tests",
        "training/tests",
    ):
        files.extend((ROOT / directory).rglob("*.py"))
    for directory in ("inference", "training"):
        files.extend((ROOT / directory).glob("*.py"))
    for directory in (
        "artifacts/models/20260926T204848-mlp-1bcd8acd",
        "artifacts/datasets/1bcd8acdc1fab6f9",
    ):
        files.extend(p for p in (ROOT / directory).iterdir() if p.is_file())
    for source in files:
        destination = target / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    print("Curated inference build context: .runtime/phase10/build-context")


if __name__ == "__main__":
    main()
