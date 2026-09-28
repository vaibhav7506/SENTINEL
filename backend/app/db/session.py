from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.readiness_timeout_seconds,
        connect_args={
            "connect_timeout": max(1, int(settings.readiness_timeout_seconds)),
            "options": (
                "-c statement_timeout=10000 -c idle_in_transaction_session_timeout=30000 "
                "-c tcp_keepalives_idle=30 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=3"
            ),
        },
        hide_parameters=True,
    )
