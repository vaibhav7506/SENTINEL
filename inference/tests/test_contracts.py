"""Explicit contract fixtures; never treated as live predictive performance."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.core.config import Settings
from app.models import FeatureWindow
from inference.alerts import deliver, slack_payload, validate_destination
from inference.scoring import Scorer
from inference.summaries import Selection, summarize
from inference.worker import alert_timing, eligible

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scorer():
    return Scorer(ROOT / "artifacts/models/20260926T204848-mlp-1bcd8acd")


def test_frozen_scores_and_actual_threshold_crossing_shap(scorer):
    metadata = scorer.metadata
    rows = [
        json.loads(line)
        for line in (scorer.dataset_path / "samples.jsonl").read_text().splitlines()
    ]
    saved = [
        json.loads(line)
        for line in (ROOT / "artifacts/models" / metadata["version"] / "scores.jsonl")
        .read_text()
        .splitlines()
    ]
    for score in saved:
        row = next(
            r
            for r in rows
            if r["host_id"] == score["host_id"] and r["window_end"] == score["window_end"]
        )
        actual = scorer.score(row["feature_values"], False)
        assert actual["failure_probability"] == pytest.approx(
            score["failure_probability"], rel=1e-5
        )
        assert actual["anomaly_score"] == pytest.approx(score["anomaly_score"], rel=1e-5)
    high = next(score for score in saved if score["warning"])
    row = next(
        r for r in rows if r["host_id"] == high["host_id"] and r["window_end"] == high["window_end"]
    )
    result = scorer.score(row["feature_values"])
    assert result["shap"]["status"] == "complete"
    assert abs(result["shap"]["additivity_residual"]) < 1e-5
    assert len(result["top_drivers"]) == 10


def test_invalid_vectors_are_rejected(scorer):
    with pytest.raises(ValueError):
        scorer.score([None])
    with pytest.raises(ValueError):
        scorer.score([np.inf] * 209)


def test_eligible_excludes_stale_future_and_schema_mismatch(scorer):
    now = datetime.now(UTC)
    schema = scorer.metadata["feature_schema"]
    window = FeatureWindow(
        window_end=now,
        window_start=now - timedelta(seconds=900),
        schema_version=schema["version"],
        schema_hash=schema["hash"],
        feature_names=schema["names"],
        feature_values=[None] * 209,
        features=dict.fromkeys(schema["names"]),
        quality={"metrics": {"cpu_usage_percent": {"coverage_seconds": 800}}},
    )
    assert eligible(window, scorer.metadata, now, 90)
    assert not eligible(window, scorer.metadata, now + timedelta(seconds=91), 90)
    assert not eligible(window, scorer.metadata, now - timedelta(seconds=1), 90)
    window.schema_hash = "wrong"
    assert not eligible(window, scorer.metadata, now, 90)


def test_delivery_timing_never_calls_late_alert_prefailure():
    onset = datetime.now(UTC)
    assert alert_timing(None, onset, onset)["lead_seconds"] is None
    assert alert_timing(onset, onset, onset)["status"] == "after_degradation"
    assert (
        alert_timing(onset - timedelta(seconds=120), onset, onset + timedelta(seconds=60))[
            "lead_seconds"
        ]
        == 120
    )
    assert (
        alert_timing(onset - timedelta(seconds=120), onset, onset - timedelta(seconds=60))["status"]
        == "outside_forecast_horizon"
    )


@pytest.mark.asyncio
async def test_summaries_fallback_and_restrict_provider_to_evidence():
    facts = [
        "Supplied host and probability",
        "Separate anomaly score",
        "Poor held-out model",
        "Supplied probe fact",
    ]

    class Provider:
        async def select(self, supplied):
            return Selection(evidence_ids=[3], checks=["service"])

    result = await summarize(facts, Provider())
    assert result["evidence"] == facts
    assert result["source"] == "llm_selected_supplied_facts"

    class Invalid:
        async def select(self, supplied):
            return Selection(evidence_ids=[999], checks=["restart_production"])

    result = await summarize(facts, Invalid())
    assert result["source"] == "deterministic_fallback"
    assert result["evidence"] == facts

    class Unavailable:
        async def select(self, supplied):
            raise httpx.ReadTimeout("fixture outage")

    assert (await summarize(facts, Unavailable()))["source"] == "deterministic_fallback"
    assert Settings(_env_file=None, llm_base_url="").llm_base_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [(200, "delivered"), (503, "retry"), (400, "failed")])
async def test_delivery_success_rejection_and_unknown(code, status):
    async def handler(request):
        assert request.headers["Idempotency-Key"] == "fixture-id"
        return httpx.Response(code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await deliver(client, "http://127.0.0.1:8091/generic", "generic", "fixture-id", {})
        )[0] == status

    async def timeout(request):
        raise httpx.ReadTimeout("fixture timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        assert (
            await deliver(client, "http://127.0.0.1:8091/generic", "generic", "fixture-id", {})
        )[0] == "unknown"


def test_webhook_scheme_and_slack_core_fields():
    with pytest.raises(ValueError):
        validate_destination("http://remote.example/webhook")
    payload = {
        "incident_id": "fixture",
        "host": "fixture",
        "service": "fixture",
        "failure_probability": 0.9,
        "anomaly_score": 0.1,
        "horizon_seconds": 600,
        "forecast_valid_until": "fixture",
        "model_validation_warning": "Poor model",
        "dashboard_link": "http://127.0.0.1:5173",
        "top_drivers": [],
        "summary": {"summary": "Supplied facts " * 1000},
    }
    formatted = slack_payload(payload)
    text = " ".join(block["text"]["text"] for block in formatted["blocks"])
    assert "http://127.0.0.1:5173" in text and "Poor model" in text and "600s" in text
    assert all(len(block["text"]["text"]) <= 2900 for block in formatted["blocks"])
