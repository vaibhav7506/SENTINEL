"""Approval boundaries and optional adapter configuration."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models import ModelVersion, Prediction
from app.services.proposals import ProposalService
from app.services.runbookos import MockRunbookOSAdapter, ProposalReceipt, ProposalRequest


@pytest.mark.asyncio
async def test_disabled_is_independent_of_database_and_adapter():
    engine, adapter = MagicMock(), MagicMock()
    service = ProposalService(Settings(_env_file=None), engine, adapter)
    await service.initialize()
    assert await service.cycle() == 0
    engine.assert_not_called()
    adapter.submit.assert_not_called()


@pytest.mark.asyncio
async def test_mock_intake_is_idempotent_and_never_approves():
    request = ProposalRequest(
        proposal_id=uuid4(),
        incident_id=uuid4(),
        prediction_id=uuid4(),
        host="fixture",
        reason="Request human review; no confirmed root cause",
        failure_probability=0.99,
        forecast_valid_until=datetime.now(UTC) + timedelta(seconds=600),
        model_validation_warning="Operational prediction remains unproven",
    )
    a, b = MockRunbookOSAdapter(), MockRunbookOSAdapter()
    assert await a.submit(request) == await b.submit(request)
    receipt = await a.submit(request)
    assert receipt.approval_status == "pending" and receipt.execution_status == "not_started"
    with pytest.raises(ValidationError):
        ProposalReceipt(
            reference="mock://fixture", approval_status="approved", execution_status="succeeded"
        )
    with pytest.raises(ValidationError):
        ProposalRequest(**{**request.model_dump(), "suggested_action": "restart_deployment"})
    with pytest.raises(ValidationError):
        ProposalRequest(**{**request.model_dump(), "human_approval_required": False})


def test_config_hides_secrets_and_rejects_unimplemented_http():
    s = Settings(_env_file=None, runbookos_api_key="phase7-secret", runbookos_base_url="")
    assert s.runbookos_base_url is None and "phase7-secret" not in repr(s)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runbookos_adapter="http")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runbookos_proposal_threshold=1.1)


@pytest.mark.parametrize(
    "offset,horizon,probability,expected",
    [
        (2, 600, 0.999, True),
        (2, 600, 0.99, False),
        (121, 600, 0.999, False),
        (60, 30, 0.999, False),
        (-10, 600, 0.999, False),
    ],
)
def test_fresh_probability_and_forecast_gate(offset, horizon, probability, expected):
    now = datetime.now(UTC)
    p = Prediction(
        probability=probability,
        predicted_at=now - timedelta(seconds=offset),
        feature_window_end=now - timedelta(seconds=offset),
        horizon_seconds=horizon,
    )
    model = ModelVersion(threshold=0.995)
    service = ProposalService(
        Settings(_env_file=None, runbookos_proposal_threshold=0.9), MagicMock()
    )
    assert service.eligible(p, model, now) is expected
