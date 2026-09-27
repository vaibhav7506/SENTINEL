"""Read-only proposal review; approval and execution belong to RunbookOS."""

from typing import Any

from fastapi import APIRouter, Query, Request

from app.api.predictions import records
from app.core.config import Settings
from app.models import RemediationProposal

router = APIRouter(tags=["governed proposals"])


@router.get("/remediation/status")
async def status(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {
        "enabled": settings.runbookos_enabled,
        "adapter": "mock" if settings.runbookos_enabled else "disabled",
        "proposal_threshold": settings.runbookos_proposal_threshold,
        "suggested_actions": ["collect_diagnostics"],
        "human_approval_required": True,
        "execution_owner": "RunbookOS",
        "real_runbookos_intake_implemented": False,
        "detail": "Mock intake only; no human approval or execution is simulated",
    }


@router.get("/remediation/proposals")
async def proposals(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    return await records(request, RemediationProposal, limit)
