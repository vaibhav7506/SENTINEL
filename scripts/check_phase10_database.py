"""Run migration and persistence cohorts against uniquely created disposable databases."""

# ruff: noqa: E402
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import psycopg
from cryptography.fernet import Fernet
from psycopg import sql

from app.core.config import Settings


def main() -> None:
    (ROOT / ".runtime/phase3").mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=ROOT / ".env")
    if settings.configured_database_url.get_secret_value() or settings.environment == "production":
        raise SystemExit("Disposable cohorts refuse managed or production database settings")
    cohorts = [
        (
            3,
            "backend",
            ["backend/tests/test_feature_database.py", "backend/tests/test_observer_database.py"],
        ),
        (
            6,
            "inference",
            ["inference/tests/test_database.py", "inference/tests/test_registration_database.py"],
        ),
        (7, "inference", ["backend/tests/test_proposal_database.py"]),
        (8, "backend", ["backend/tests/test_console_database.py"]),
        (
            2,
            "backend",
            ["backend/tests/test_saas_database.py", "backend/tests/test_push_failure_database.py"],
        ),
    ]
    with psycopg.connect(
        host="127.0.0.1" if settings.postgres_host == "localhost" else settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=10,
        sslmode=settings.postgres_sslmode,
    ) as connection:
        for phase, runtime, tests in cohorts:
            database = f"sentinel_phase{phase}_test_" + uuid4().hex[:12]
            environment = {
                **os.environ,
                "POSTGRES_HOST": "127.0.0.1"
                if settings.postgres_host == "localhost"
                else settings.postgres_host,
                "POSTGRES_PORT": str(settings.postgres_port),
                "POSTGRES_DB": database,
                "POSTGRES_USER": settings.postgres_user,
                "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
                "INTEGRATION_ENCRYPTION_KEY": settings.integration_encryption_key.get_secret_value()
                or Fernet.generate_key().decode(),
                "SENTINEL_TEST_DATABASE": "1",
                "REDIS_URL": "redis://127.0.0.1:16379/15",
                "SENTINEL_TEST_EVIDENCE": ".runtime/phase3/proposal-replay.json",
            }
            python = (
                str(ROOT / runtime / ".venv/Scripts/python.exe")
                if sys.platform == "win32"
                else sys.executable
            )
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
                    "--tb=short",
                    "--show-capture=no",
                    "-p",
                    "no:cacheprovider",
                    "--basetemp=.runtime/phase3/cohort-tmp",
                ],
                ["alembic", "-c", "backend/alembic.ini", "check"],
            ]
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            try:
                for args in commands:
                    subprocess.run([python, "-m", *args], cwd=ROOT, env=environment, check=True)
                rejected = subprocess.run(
                    [python, "-m", "alembic", "-c", "backend/alembic.ini", "downgrade", "base"],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                if rejected.returncode == 0 or "Restore a verified backup" not in rejected.stderr:
                    raise RuntimeError("Forward-only migration must refuse destructive downgrade")
                subprocess.run(
                    [python, "-m", "alembic", "-c", "backend/alembic.ini", "check"],
                    cwd=ROOT,
                    env=environment,
                    check=True,
                )
                print("PASS isolated cohort: " + ", ".join(tests), flush=True)
            finally:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )


if __name__ == "__main__":
    main()
