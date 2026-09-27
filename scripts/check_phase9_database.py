"""Full DB suites and migration round trips in uniquely named disposable databases."""

# ruff: noqa: E402
import argparse
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import psycopg
from psycopg import sql

from app.core.config import Settings


def main(selected_phase: int | None = None) -> None:
    settings = Settings(_env_file=ROOT / ".env")
    cohorts = [
        (
            3,
            "backend",
            ["backend/tests/test_feature_database.py", "backend/tests/test_observer_database.py"],
        ),
        (6, "inference", ["inference/tests/test_database.py"]),
        (7, "inference", ["backend/tests/test_proposal_database.py"]),
        (8, "backend", ["backend/tests/test_console_database.py"]),
    ]
    with psycopg.connect(
        host="127.0.0.1",
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        for phase, runtime, tests in cohorts:
            if selected_phase is not None and phase != selected_phase:
                continue
            database = f"sentinel_phase{phase}_test_" + uuid4().hex[:12]
            environment = {
                **os.environ,
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": str(settings.postgres_port),
                "POSTGRES_DB": database,
                "POSTGRES_USER": settings.postgres_user,
                "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
                "SENTINEL_TEST_DATABASE": "1",
                "SENTINEL_TEST_EVIDENCE": "docs/phase-9-proposal-replay.json",
            }
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            try:
                commands = [
                    ["alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                    [
                        "pytest",
                        "-c",
                        "inference/pyproject.toml"
                        if runtime == "inference"
                        else "backend/pyproject.toml",
                        *tests,
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        "--basetemp=.runtime/phase9/db-tmp",
                    ],
                    ["alembic", "-c", "backend/alembic.ini", "check"],
                    ["alembic", "-c", "backend/alembic.ini", "downgrade", "base"],
                    ["alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                    ["alembic", "-c", "backend/alembic.ini", "check"],
                ]
                python = ROOT / runtime / ".venv/Scripts/python.exe"
                for arguments in commands:
                    subprocess.run(
                        [str(python), "-m", *arguments], cwd=ROOT, env=environment, check=True
                    )
                print(
                    f"PASS cohort {phase}: persistence, schema parity, base/head round trip",
                    flush=True,
                )
            finally:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, choices=[3, 6, 7, 8])
    main(parser.parse_args().phase)
