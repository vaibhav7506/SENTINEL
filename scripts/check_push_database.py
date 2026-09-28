"""Run real queue/ingestion contracts against a disposable database and Redis DB 15."""

# ruff: noqa: E402
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
    if settings.configured_database_url.get_secret_value() or settings.environment == "production":
        raise SystemExit("Disposable queue checks refuse managed or production database settings")
    database = "sentinel_phase3_push_test_" + uuid4().hex[:12]
    fixture = "sentinel-phase3-fixture-" + uuid4().hex[:12]
    env = {
        **os.environ,
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_DB": database,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "INTEGRATION_ENCRYPTION_KEY": settings.integration_encryption_key.get_secret_value(),
        "REDIS_URL": "redis://127.0.0.1:16379/15",
        "SENTINEL_TEST_DATABASE": "1",
        "SENTINEL_FIXTURE_WORKER": fixture,
        "SENTINEL_QUEUE": "phase3-fixture",
    }
    private = ROOT / ".runtime/phase3/test-worker.env"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(
        "\n".join(
            [
                "POSTGRES_HOST=timescaledb",
                "POSTGRES_PORT=5432",
                "POSTGRES_DB=" + database,
                "POSTGRES_USER=" + settings.postgres_user,
                "POSTGRES_PASSWORD=" + settings.postgres_password.get_secret_value(),
                "REDIS_URL=redis://redis:6379/15",
                "SENTINEL_QUEUE=phase3-fixture",
                "SAAS_ENABLED=true",
                "ENVIRONMENT=test",
                "RUNBOOKOS_ENABLED=false",
                "INTEGRATION_ENCRYPTION_KEY="
                + settings.integration_encryption_key.get_secret_value(),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    created = []
    with psycopg.connect(
        host="127.0.0.1",
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
    ) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:

            def run(args, **kwargs):
                return subprocess.run(args, cwd=ROOT, env=env, check=True, **kwargs)

            run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"])
            for name, command in (
                (
                    fixture,
                    ["worker", "--loglevel=WARNING", "--concurrency=2", "-Q", "phase3-fixture"],
                ),
                (fixture + "-beat", ["beat", "--loglevel=WARNING", "--schedule=/tmp/phase3-beat"]),
            ):
                result = run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        name,
                        "--network",
                        "sentinel_default",
                        "--env-file",
                        str(private),
                        "sentinel-push:phase3",
                        "/queue/.venv/bin/celery",
                        "-A",
                        "celery_app:app",
                        *command,
                    ],
                    capture_output=True,
                    text=True,
                )
                created.append(result.stdout.strip())
            run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-c",
                    "inference/pyproject.toml",
                    "backend/tests/test_push_database.py",
                    "backend/tests/test_saas_database.py",
                    "backend/tests/test_push_failure_database.py",
                    "-q",
                    "--tb=short",
                    "--show-capture=no",
                    "-p",
                    "no:cacheprovider",
                    "--basetemp=.runtime/phase3/db-tests",
                ]
            )
            run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "check"])
            print("PASS disposable 100-host queue, isolation, retry, routing and schema checks")
        finally:
            for container in created:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=True)
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            private.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
