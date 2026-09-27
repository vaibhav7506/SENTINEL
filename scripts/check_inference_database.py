# ruff: noqa: E402
"""Create/drop an isolated fixture database; preserve actual live Sentinel data."""

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
    database = "sentinel_phase6_test_" + uuid4().hex[:12]
    environment = {
        **os.environ,
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "POSTGRES_DB": database,
        "SENTINEL_TEST_DATABASE": "1",
    }
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            commands = [
                ["alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                [
                    "pytest",
                    "-c",
                    "inference/pyproject.toml",
                    "inference/tests/test_database.py",
                    "-q",
                    "--tb=short",
                ],
                ["alembic", "-c", "backend/alembic.ini", "check"],
                [
                    "alembic",
                    "-c",
                    "backend/alembic.ini",
                    "downgrade",
                    "0001_timescaledb",
                ],
                ["alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                ["alembic", "-c", "backend/alembic.ini", "check"],
            ]
            for arguments in commands:
                subprocess.run(
                    [sys.executable, "-m", *arguments],
                    cwd=ROOT,
                    env=environment,
                    check=True,
                )
            print("PASS isolated inference persistence, restart dedup and migration round trip")
        finally:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


if __name__ == "__main__":
    main()
