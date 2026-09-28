"""Exclusive, forward-only operator migration; never run in an API request."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.saas.integrations import encrypt_destination


def main() -> None:
    settings = Settings()
    if settings.environment == "production" and os.getenv("SENTINEL_BACKUP_CONFIRMED") != "true":
        raise SystemExit("Confirm a verified database backup before the production migration")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    engine = create_engine(settings.database_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(736284067)"))
            connection.commit()
            if not acquired:
                raise RuntimeError("Another migration is active")
            try:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                connection.commit()
                extension = connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
                )
                hypertables = set(
                    connection.scalars(
                        text(
                            "SELECT hypertable_name FROM timescaledb_information.hypertables "
                            "WHERE hypertable_schema='public'"
                        )
                    )
                )
                if not extension or not {"metrics", "feature_windows"} <= hypertables:
                    raise RuntimeError("Required Timescale hypertables missing")
                connection.execute(
                    text("SELECT remove_retention_policy('metrics',if_exists=>true)")
                )
                connection.execute(
                    text(
                        "SELECT add_retention_policy('metrics',"
                        "drop_after=>make_interval(days=>:days),if_not_exists=>true)"
                    ),
                    {"days": settings.metric_retention_days},
                )
                rows = connection.execute(
                    text("SELECT id,account_id,destination FROM alert_channels FOR UPDATE")
                ).all()
                for identifier, account, destination in rows:
                    if not destination.startswith("enc:v1:"):
                        encrypted = encrypt_destination(destination, account, settings)
                        connection.execute(
                            text("UPDATE alert_channels SET destination=:d WHERE id=:id"),
                            {"d": encrypted, "id": identifier},
                        )
                connection.commit()
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(736284067)"))
                connection.commit()
        print("Migration 0008_push_pipeline and Timescale storage checks passed")
    except Exception:
        raise SystemExit(
            "Predeploy failed; application rollout must stop. Inspect the database privately."
        ) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
