"""Verify real agent HTTP delivery against a disposable local database and API."""

# ruff: noqa: E402
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
import psycopg
from psycopg import sql

from app.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    settings = Settings(_env_file=ROOT / ".env")
    if settings.configured_database_url.get_secret_value() or settings.environment == "production":
        raise SystemExit("HTTP fixtures refuse managed or production databases")
    database = "sentinel_phase3_http_test_" + uuid4().hex[:12]
    server = "http://127.0.0.1:18005"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "backend"),
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_DB": database,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "INTEGRATION_ENCRYPTION_KEY": settings.integration_encryption_key.get_secret_value(),
        "REDIS_URL": "redis://127.0.0.1:16379/15",
        "SAAS_ENABLED": "true",
        "ENVIRONMENT": "test",
        "CHAOS_ENABLED": "false",
        "RUNBOOKOS_ENABLED": "false",
        "LLM_PROVIDER": "disabled",
        "PROMETHEUS_READINESS_MODE": "disabled",
        "SAAS_PUBLIC_API_URL": server,
        "SAAS_ALLOWED_ORIGINS": '["http://127.0.0.1:18005","http://127.0.0.1:18006"]',
        "SENTINEL_AGENT_SMOKE_API_URL": server,
    }
    output = ROOT / ".runtime/phase3"
    output.mkdir(parents=True, exist_ok=True)
    process = None
    frontend = None
    finished = output / ("finish-browser-" + uuid4().hex)
    with psycopg.connect(
        host="127.0.0.1",
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                check=True,
            )
            with (output / "fixture-api-private.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "18005",
                        "--loop",
                        "app.core.event_loop:create_event_loop",
                        "--no-access-log",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                deadline = time.monotonic() + 45
                with httpx.Client(timeout=2) as client:
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("Fixture API exited; inspect its private log")
                        try:
                            if client.get(server + "/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(1)
                    else:
                        raise RuntimeError("Fixture API did not become ready")
                if args.browser:
                    email = "sentinel-browser-check@local.invalid"
                    password = "LocalOnlySentinel-Check-20260928!"
                    with httpx.Client(base_url=server, timeout=10) as client:
                        response = client.post(
                            "/auth/register",
                            json={
                                "email": email,
                                "password": password,
                                "account_name": "Disposable browser verification",
                            },
                        )
                        response.raise_for_status()
                    metadata = {
                        "url": "http://127.0.0.1:18006",
                        "email": email,
                        "finish_file": str(finished),
                        "disposable_database": database,
                    }
                    (output / "browser-fixture.json").write_text(
                        json.dumps(metadata, indent=2), encoding="utf-8"
                    )
                    with (output / "fixture-frontend-private.log").open(
                        "w", encoding="utf-8"
                    ) as ui_log:
                        frontend = subprocess.Popen(
                            [
                                "node",
                                str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                                "--host",
                                "127.0.0.1",
                                "--port",
                                "18006",
                                "--strictPort",
                            ],
                            cwd=ROOT / "frontend",
                            env={**env, "SENTINEL_DEV_API_URL": server},
                            stdout=ui_log,
                            stderr=ui_log,
                        )
                        print("Disposable browser fixture: http://127.0.0.1:18006", flush=True)
                        deadline = time.monotonic() + 600
                        while not finished.exists() and time.monotonic() < deadline:
                            if frontend.poll() is not None:
                                raise RuntimeError("Fixture frontend exited; inspect private log")
                            time.sleep(1)
                        if not finished.exists():
                            raise RuntimeError(
                                "Browser fixture timed out; resources are being cleaned"
                            )
                else:
                    subprocess.run(
                        [sys.executable, "scripts/check_real_agent.py"],
                        cwd=ROOT,
                        env=env,
                        check=True,
                    )
                    print("PASS disposable HTTP API and real Windows agent lifecycle")
        finally:
            if frontend is not None and frontend.poll() is None:
                frontend.terminate()
                frontend.wait(timeout=15)
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
            finished.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
