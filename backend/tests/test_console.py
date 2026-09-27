"""Protect the distinctions the console makes between scores and observed facts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.console import host_status, record, telemetry, useful_lead
from app.core.config import Settings
from app.models import Alert, Prediction, RemediationProposal, ServiceObservation

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def observation(age=0, breached=False):
    return ServiceObservation(observed_at=NOW - timedelta(seconds=age), breached=breached)


@pytest.mark.parametrize(
    "sample,active,status",
    [
        (None, False, "unknown"),
        (observation(91), False, "unknown"),
        (observation(-1), False, "unknown"),
        (observation(), False, "healthy"),
        (observation(breached=True), False, "degraded"),
        (None, True, "degraded"),
    ],
)
def test_observed_health_requires_fresh_evidence(sample, active, status):
    assert host_status(NOW, sample, active) == status


@pytest.mark.parametrize(
    "prediction_age,window_age,horizon,delivery_age,lead",
    [
        (120, 130, 600, None, 120),
        (59, 70, 600, None, None),
        (120, 700, 600, None, None),
        (120, 130, 600, 90, 90),
        (120, 130, 600, 30, None),
        (120, 130, 600, 150, None),
        (120, -5, 600, None, None),
    ],
)
def test_warning_lead_requires_pre_onset_delivery_and_covering_horizon(
    prediction_age, window_age, horizon, delivery_age, lead
):
    prediction = Prediction(
        predicted_at=NOW - timedelta(seconds=prediction_age),
        feature_window_end=NOW - timedelta(seconds=window_age),
        horizon_seconds=horizon,
    )
    delivered = NOW - timedelta(seconds=delivery_age) if delivery_age is not None else None
    assert useful_lead(prediction, NOW, delivered) == lead


def test_console_omits_transport_payloads_and_proposal_parameters():
    assert "payload" not in record(Alert(payload={"destination": "private"}))
    assert "parameters" not in record(RemediationProposal(parameters={"internal": "value"}))


@pytest.mark.parametrize("mode", ["fresh", "stale", "offline", "nonfinite"])
async def test_raw_telemetry_is_real_finite_and_failure_is_explicit(monkeypatch, mode):
    seen = []

    def respond(request):
        seen.append(request)
        if mode == "offline":
            return httpx.Response(503)
        if request.url.path.endswith("query_range"):
            values = [[NOW.timestamp(), "42.5"], [NOW.timestamp(), "NaN"]]
            if mode == "nonfinite":
                values = [[NOW.timestamp(), "Inf"]]
            result = [{"metric": {"__name__": "sentinel_host_cpu_usage_percent"}, "values": values}]
        else:
            age = 120 if mode == "stale" else 1
            result = [{"value": [NOW.timestamp(), str(NOW.timestamp() - age)]}]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.api.console.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )
    application = SimpleNamespace(state=SimpleNamespace(settings=Settings(_env_file=None)))
    request = Request({"type": "http", "app": application})
    result = await telemetry(request, 'host"injection', NOW)
    assert result["status"] == (
        "unavailable" if mode == "offline" else "stale" if mode == "stale" else "available"
    )
    if mode == "fresh":
        assert result["series"][0]["values"] == [[NOW.timestamp(), 42.5]]
        assert 'host_id="host\\"injection"' in seen[0].url.params["query"]
    if mode == "nonfinite":
        assert result["series"][0]["values"] == []


async def test_missing_host_returns_404_before_any_telemetry(monkeypatch):
    from app.api.console import host_detail

    session = AsyncMock()
    session.get.return_value = None
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = session
    monkeypatch.setattr("app.api.console.async_sessionmaker", lambda engine: factory)
    application = SimpleNamespace(state=SimpleNamespace(engine=object()))
    with pytest.raises(HTTPException) as error:
        await host_detail("missing", Request({"type": "http", "app": application}))
    assert error.value.status_code == 404
    session.scalar.assert_not_called()
