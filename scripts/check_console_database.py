# ruff: noqa: E402
"""Run console joins in a disposable database, preserving all live records."""

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
    database = "sentinel_phase8_test_" + uuid4().hex[:12]
    environment = {
        **os.environ,
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_DB": database,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "SENTINEL_TEST_DATABASE": "1",
    }
    with psycopg.connect(
        host="127.0.0.1",
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            for args in [
                ["alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                [
                    "pytest",
                    "-c",
                    "backend/pyproject.toml",
                    "backend/tests/test_console_database.py",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
            ]:
                subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=environment, check=True)
            print("PASS isolated console joins, list limits and advance delivery timing")
        finally:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


if __name__ == "__main__":
    main()
