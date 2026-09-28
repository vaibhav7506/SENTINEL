import asyncio
from typing import Literal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas.health import DependencyHealth, ReadinessResponse


class ReadinessChecker:
    def __init__(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        timeout: float,
        prometheus_mode: Literal["local", "query", "disabled"] = "local",
    ) -> None:
        self.engine = engine
        self.client = client
        self.timeout = timeout
        self.prometheus_mode = prometheus_mode

    async def database(self) -> DependencyHealth:
        try:
            async with asyncio.timeout(self.timeout):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    version = await connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                    )
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
            if not version:
                return DependencyHealth(status="down", detail="TimescaleDB extension missing")
            if revision != "0008_push_pipeline":
                return DependencyHealth(status="down", detail="Database migration missing")
            return DependencyHealth(status="up", detail="PostgreSQL and TimescaleDB reachable")
        except Exception:
            # Driver errors can contain credentials; expose only a fixed safe message.
            return DependencyHealth(status="down", detail="Database check failed")

    async def prometheus(self) -> DependencyHealth:
        try:
            async with asyncio.timeout(self.timeout):
                response = (
                    await self.client.get("/api/v1/query", params={"query": "vector(1)"})
                    if self.prometheus_mode == "query"
                    else await self.client.get("/-/ready")
                )
                response.raise_for_status()
                if self.prometheus_mode == "query":
                    payload = response.json()
                    if (
                        payload.get("status") != "success"
                        or payload.get("data", {}).get("resultType") != "vector"
                        or not payload["data"].get("result")
                    ):
                        raise ValueError("Prometheus query check failed")
            return DependencyHealth(status="up", detail="Prometheus ready")
        except Exception:
            return DependencyHealth(status="down", detail="Prometheus check failed")

    async def check(self) -> ReadinessResponse:
        if self.prometheus_mode == "disabled":
            database = await self.database()
            return ReadinessResponse(
                status="ready" if database.status == "up" else "not_ready",
                dependencies={"database": database},
            )
        database, prometheus = await asyncio.gather(self.database(), self.prometheus())
        dependencies = {"database": database, "prometheus": prometheus}
        ready = all(item.status == "up" for item in dependencies.values())
        return ReadinessResponse(
            status="ready" if ready else "not_ready", dependencies=dependencies
        )
