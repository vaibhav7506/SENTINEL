import asyncio
import hmac
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.chaos import router as chaos_router
from app.api.console import router as console_router
from app.api.health import router
from app.api.predictions import router as predictions_router
from app.api.proposals import router as proposals_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.metrics import instrument
from app.saas.auth import install_authentication
from app.saas.auth import router as auth_router
from app.saas.ingestion import authenticate_agent
from app.saas.ingestion import router as ingestion_router
from app.saas.installer import router as installer_router
from app.saas.operations import router as operations_router
from app.services.prometheus import create_prometheus_client
from app.services.readiness import ReadinessChecker

logger = logging.getLogger("sentinel.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.log_level)
        engine = create_database_engine(config)
        application.state.engine = engine
        application.state.settings = config
        from redis.asyncio import Redis

        application.state.redis = Redis.from_url(
            config.redis_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
        )
        async with create_prometheus_client(config, config.readiness_timeout_seconds) as client:
            application.state.readiness = ReadinessChecker(
                engine, client, config.readiness_timeout_seconds, config.prometheus_readiness_mode
            )
            logger.info("API started", extra={"component": "sentinel-api"})
            try:
                yield
            finally:
                await application.state.redis.aclose()
                await engine.dispose()
                logger.info("API stopped", extra={"component": "sentinel-api"})

    application = FastAPI(title="Sentinel API", version="0.1.0", lifespan=lifespan)
    application.state.settings = config
    application.include_router(router)
    application.include_router(chaos_router)
    application.include_router(predictions_router)
    application.include_router(proposals_router)
    application.include_router(console_router)
    if config.saas_enabled:
        application.include_router(auth_router)
        application.include_router(operations_router)
        application.include_router(installer_router)
        application.include_router(ingestion_router)
    starts: deque[float] = deque(maxlen=6)
    console_reads: deque[float] = deque(maxlen=120)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic's input/context can contain tokens or arbitrary user text.
        return JSONResponse(status_code=422, content={"detail": "Invalid request values"})

    @application.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Generate IDs internally; arbitrary incoming headers never enter logs.
        request_id = str(uuid4())
        start = time.perf_counter()
        response: Response
        try:
            if (
                config.environment == "production"
                and not config.saas_enabled
                and request.url.path not in {"/health", "/ready", "/metrics"}
                and not (
                    request.url.path.startswith("/chaos/")
                    and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                )
                and not hmac.compare_digest(
                    request.headers.get("authorization", "").encode(),
                    ("Bearer " + config.api_read_token.get_secret_value()).encode(),
                )
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                    headers={"WWW-Authenticate": "Bearer", "X-Request-ID": request_id},
                )
            if request.url.path.startswith("/console/"):
                now = time.monotonic()
                while console_reads and console_reads[0] <= now - 60:
                    console_reads.popleft()
                if len(console_reads) == 120:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Console rate limit"},
                        headers={"Retry-After": "60", "X-Request-ID": request_id},
                    )
                console_reads.append(now)
            if len(request.url.path) > 2048 or len(request.url.query) > 4096:
                response = JSONResponse(status_code=414, content={"detail": "Request URI too long"})
            elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                if request.url.path in {"/agent/v1/metrics", "/agent/metrics"}:
                    request.state.agent_identity = await authenticate_agent(request)
                # Stream with a byte limit even when Content-Length is absent or dishonest.
                body = b""
                oversized = False
                maximum = 131072 if request.url.path == "/agent/v1/metrics" else 16384
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > maximum:
                            oversized = True
                            break
                        body += chunk
                if oversized:
                    response = JSONResponse(
                        status_code=413, content={"detail": "Request too large"}
                    )
                else:
                    request._body = body
                    if request.url.path.startswith("/chaos/"):
                        from app.api.chaos import authorize

                        authorize(request, request.headers.get("authorization"))
                    if request.url.path == "/chaos/experiments" and request.method == "POST":
                        now = time.monotonic()
                        while starts and starts[0] <= now - 60:
                            starts.popleft()
                        if len(starts) == 6:
                            return JSONResponse(
                                status_code=429,
                                content={"detail": "Experiment rate limit"},
                                headers={"Retry-After": "60", "X-Request-ID": request_id},
                            )
                        starts.append(now)
                    async with asyncio.timeout(10):
                        response = await call_next(request)
            else:
                async with asyncio.timeout(10):
                    response = await call_next(request)
        except TimeoutError:
            response = JSONResponse(status_code=504, content={"detail": "Request timed out"})
        except Exception as error:
            from fastapi import HTTPException

            if isinstance(error, HTTPException):
                response = JSONResponse(
                    status_code=error.status_code,
                    content={"detail": error.detail},
                    headers=error.headers,
                )
            else:
                if isinstance(error, SQLAlchemyError):
                    request.app.state.database_errors.inc()
                logger.error("API request failed", extra={"request_id": request_id})
                response = JSONResponse(
                    status_code=503 if isinstance(error, SQLAlchemyError) else 500,
                    content={"detail": "Service unavailable"},
                )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": getattr(request.scope.get("route"), "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        )
        return response

    instrument(application)
    if config.saas_enabled:
        install_authentication(application, config)
    return application


app = create_app()
