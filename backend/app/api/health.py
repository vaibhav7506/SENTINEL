from fastapi import APIRouter, Request, Response

from app.schemas.health import ReadinessResponse
from app.services.readiness import ReadinessChecker

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel-api", "phase": "9"}


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    checker: ReadinessChecker = request.app.state.readiness
    result = await checker.check()
    response.status_code = 200 if result.status == "ready" else 503
    return result
