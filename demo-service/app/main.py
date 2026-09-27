import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.chaos import authorize, state
from app.chaos import router as chaos_router
from app.metrics import instrument

app = FastAPI(title="Sentinel Demo Service", version="0.2.0")
logging.getLogger("uvicorn.access").disabled = True
app.include_router(chaos_router)
app.state.chaos_starts = deque(maxlen=6)


@app.exception_handler(RequestValidationError)
async def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Invalid request values"})


@app.middleware("http")
async def bound_control(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if not request.url.path.startswith("/control/chaos"):
        return await call_next(request)
    try:
        authorize(request.headers.get("authorization"))
        body = b""
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16384:
                    return JSONResponse(status_code=413, content={"detail": "Request too large"})
                body += chunk
        request._body = body
        if request.method == "POST":
            now = time.monotonic()
            starts = app.state.chaos_starts
            while starts and starts[0] <= now - 60:
                starts.popleft()
            if len(starts) == 6:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Experiment rate limit"},
                    headers={"Retry-After": "60"},
                )
            starts.append(now)
        return await call_next(request)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
    except TimeoutError:
        return JSONResponse(status_code=408, content={"detail": "Request body timed out"})


instrument(app)


@app.get("/")
async def index() -> dict[str, str]:
    return {"service": "sentinel-demo", "message": "Demo service is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel-demo"}


@app.get("/work")
async def work(
    units: int = Query(default=100_000, ge=1, le=2_000_000),
) -> dict[str, int]:
    """Bounded real computation for generating reproducible application load."""
    failure_type, delay = state.effect()
    if failure_type == "http_error":
        raise HTTPException(503, "Controlled demo experiment")
    if failure_type == "application_latency":
        await asyncio.sleep(delay)
    checksum = await asyncio.to_thread(lambda: sum(((i * i) ^ i) % 97 for i in range(units)))
    return {"units": units, "checksum": checksum}
