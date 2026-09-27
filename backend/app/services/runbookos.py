"""Proposal intake contract only. No approval, execution, or shell operations."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: UUID
    incident_id: UUID
    prediction_id: UUID
    host: str = Field(min_length=1, max_length=128)
    suggested_action: Literal["collect_diagnostics"] = "collect_diagnostics"
    runbook_reference: Literal["collect-diagnostics:v1"] = "collect-diagnostics:v1"
    reason: str = Field(min_length=1, max_length=4096)
    failure_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    anomaly_score: float | None = Field(default=None, allow_inf_nan=False)
    forecast_valid_until: AwareDatetime
    model_validation_warning: str = Field(min_length=1, max_length=1024)
    human_approval_required: Literal[True] = True
    execution_owner: Literal["RunbookOS"] = "RunbookOS"


class ProposalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference: str = Field(min_length=1, max_length=1024)
    approval_status: Literal["pending"] = "pending"
    execution_status: Literal["not_started"] = "not_started"
    adapter_kind: Literal["mock"] = "mock"


class RunbookOSAdapter(Protocol):
    async def submit(self, proposal: ProposalRequest) -> ProposalReceipt:
        """Idempotent intake keyed by proposal_id; never approve or execute."""
        ...


class MockRunbookOSAdapter:
    """Deterministic mock acceptance, explicitly unrelated to real execution."""

    async def submit(self, proposal: ProposalRequest) -> ProposalReceipt:
        return ProposalReceipt(reference=f"mock://runbookos/proposals/{proposal.proposal_id}")
