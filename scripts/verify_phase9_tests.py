"""Run every project suite and persist compact Phase 9 evidence."""

# ruff: noqa: E402
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    (ROOT / ".runtime/phase9").mkdir(parents=True, exist_ok=True)
    suites = [
        (
            "backend",
            "backend",
            [
                "-m",
                "pytest",
                "-c",
                "backend/pyproject.toml",
                "backend/tests",
                *[
                    "--ignore=backend/tests/" + name
                    for name in [
                        "test_feature_database.py",
                        "test_observer_database.py",
                        "test_proposal_database.py",
                        "test_console_database.py",
                    ]
                ],
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp=.runtime/phase9/backend-tmp",
            ],
            ROOT,
        ),
        (
            "inference",
            "inference",
            [
                "-m",
                "pytest",
                "-c",
                "inference/pyproject.toml",
                "inference/tests",
                "--ignore=inference/tests/test_database.py",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp=.runtime/phase9/inference-tmp",
            ],
            ROOT,
        ),
        (
            "training",
            "training",
            [
                "-m",
                "pytest",
                "-c",
                "training/pyproject.toml",
                "training/tests",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp=.runtime/phase9/training-tmp",
            ],
            ROOT,
        ),
        ("agent", "agent", ["-m", "pytest", "-q", "-p", "no:cacheprovider"], ROOT / "agent"),
        (
            "demo",
            "demo-service",
            ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
            ROOT / "demo-service",
        ),
        ("legacy", ".", ["-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], ROOT),
        ("isolated_database", "backend", ["scripts/check_phase9_database.py"], ROOT),
    ]
    results = []
    for name, runtime, args, directory in suites:
        process = subprocess.run(
            [str(ROOT / runtime / ".venv/Scripts/python.exe"), *args],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        counts = re.findall(r"(\d+) passed", process.stdout)
        item = {
            "suite": name,
            "passed": sum(map(int, counts)),
            "exit_code": process.returncode,
            "skipped": sum(map(int, re.findall(r"(\d+) skipped", process.stdout))),
        }
        results.append(item)
        print(json.dumps(item), flush=True)
        if process.returncode:
            print(process.stdout[-6000:], process.stderr[-3000:])
            break
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "suites": results,
        "total_passed": sum(r["passed"] for r in results),
        "status": "passed"
        if len(results) == len(suites)
        and all(r["exit_code"] == 0 and r["skipped"] == 0 for r in results)
        else "failed",
        "frontend": "Run npm test separately; its evidence is recorded in the acceptance report",
        "isolated_database_cohorts": 4,
        "migration_round_trips": "base to head for every cohort",
    }
    (ROOT / "docs/phase-9-tests.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
