# ruff: noqa: E402
"""Test migrations and persistence in a temporary database, preserving demo data."""

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


def main() -> None:
    settings = Settings(_env_file=ROOT / ".env")
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    database = "sentinel_phase3_test_" + uuid4().hex[:12]
    environment = os.environ.copy()
    environment.update(
        POSTGRES_HOST=settings.postgres_host,
        POSTGRES_PORT=str(settings.postgres_port),
        POSTGRES_USER=settings.postgres_user,
        POSTGRES_PASSWORD=settings.postgres_password.get_secret_value(),
        POSTGRES_DB=database,
        SENTINEL_TEST_DATABASE="1",
    )
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        print("Connected; creating isolated test database", flush=True)
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            commands = [
                ["alembic", "upgrade", "head"],
                [
                    "pytest",
                    "tests/test_feature_database.py",
                    "tests/test_observer_database.py",
                    "-q",
                ],
                ["alembic", "check"],
                ["alembic", "downgrade", "0001_timescaledb"],
                ["alembic", "upgrade", "head"],
                ["alembic", "check"],
            ]
            for arguments in commands:
                subprocess.run(
                    [sys.executable, "-m", *arguments],
                    cwd=ROOT / "backend",
                    env=environment,
                    check=True,
                )
            print(
                "PASS isolated migration round trip, schema parity, "
                "immutable retries and atomic rollback"
            )
        finally:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


if __name__ == "__main__":
    main()
