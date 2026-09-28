"""Guarded additive local migration; preserve old values and save a private backup."""

# ruff: noqa: E402
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import Settings


def main() -> None:
    settings = Settings(_env_file=ROOT / ".env")
    if (
        settings.environment != "development"
        or settings.configured_database_url.get_secret_value()
        or settings.postgres_host not in {"localhost", "127.0.0.1"}
        or settings.postgres_db != "sentinel"
    ):
        raise SystemExit("This helper only supports the known local development database")
    output = ROOT / ".runtime/phase3"
    output.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, hide_parameters=True)
    compose = [
        "docker",
        "compose",
        "-p",
        "sentinel",
        "-f",
        "compose.yaml",
        "-f",
        "compose.push.yaml",
    ]

    def run(args):
        subprocess.run(args, cwd=ROOT, check=True, capture_output=True)

    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != "0007_saas_foundation":
        raise SystemExit("Expected revision 0007; no changes made")
    run(compose + ["stop", "sentinel-api", "sentinel-worker", "sentinel-observer"])
    try:
        backup = subprocess.check_output(
            [
                "docker",
                "exec",
                "sentinel-timescaledb-1",
                "pg_dump",
                "-U",
                settings.postgres_user,
                "-d",
                settings.postgres_db,
                "-Fc",
            ],
            stderr=subprocess.DEVNULL,
        )
        with (output / "pre-push-backup.dump").open("xb") as stream:
            stream.write(backup)
        with engine.begin() as connection:
            tables = list(
                connection.scalars(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename!='alembic_version' ORDER BY tablename"
                    )
                )
            )
            columns = {
                table: list(
                    connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name=:t "
                            "ORDER BY ordinal_position"
                        ),
                        {"t": table},
                    )
                )
                for table in tables
            }

            def capture():
                result = {}
                for table in tables:
                    quoted = ",".join('"' + column + '"' for column in columns[table])
                    query = text("SELECT " + quoted + ' FROM "' + table + '"')
                    rows = sorted(
                        json.dumps(tuple(row), default=str, sort_keys=True)
                        for row in connection.execute(query)
                    )
                    result[table] = {
                        "rows": len(rows),
                        "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
                    }
                return result

            before = capture()
            config = Config(str(ROOT / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            after = capture()
            if before != after:
                raise RuntimeError("Old values changed; the migration will roll back")
        (output / "migration-preservation.json").write_text(
            json.dumps(
                {
                    "revision": "0008_push_pipeline",
                    "backup_sha256": hashlib.sha256(backup).hexdigest(),
                    "before": before,
                    "after": after,
                    "all_original_values_preserved": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
        subprocess.run(
            [sys.executable, "-m", "app.saas.predeploy"], env=environment, cwd=ROOT, check=True
        )
        run(compose + ["up", "-d", "--no-deps", "--wait", "sentinel-api", "sentinel-frontend"])
        print("PASS local migration, private backup and all original values preserved")
    finally:
        subprocess.run(
            compose + ["start", "sentinel-api", "sentinel-worker", "sentinel-observer"],
            cwd=ROOT,
            capture_output=True,
        )
        engine.dispose()


if __name__ == "__main__":
    main()
