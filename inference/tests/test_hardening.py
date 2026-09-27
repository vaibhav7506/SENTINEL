import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from prometheus_client import CollectorRegistry, Counter, generate_latest

from app.core.config import Settings
from app.models import FeatureWindow
from inference.alerts import deliver
from inference.scoring import ROOT, Scorer, validate_artifacts
from inference.summaries import HTTPProvider, summarize
from inference.worker import Worker, eligible

MODEL = ROOT / "artifacts/models/20260926T204848-mlp-1bcd8acd"


@pytest.mark.parametrize("corruption", ["manifest", "digest", "path", "threshold", "names"])
def test_bad_model_contract_fails_closed(corruption):
    metadata = json.loads((MODEL / "metadata.json").read_text())
    if corruption == "manifest":
        metadata["artifact_sha256"].pop("mlp.pt")
    elif corruption == "digest":
        metadata["artifact_sha256"]["mlp.pt"] = "0" * 64
    elif corruption == "path":
        metadata["artifact_sha256"]["../metadata.json"] = "0" * 64
    elif corruption == "threshold":
        metadata["threshold"] = float("nan")
    else:
        metadata["feature_schema"]["names"][1] = metadata["feature_schema"]["names"][0]
    with pytest.raises(ValueError):
        validate_artifacts(MODEL.resolve(), metadata)


def test_loader_rejects_external_artifact_directory(tmp_path):
    with pytest.raises(ValueError, match="trusted"):
        Scorer(tmp_path)


@pytest.mark.parametrize("corruption", ["bytes", "schema", "normalizer"])
def test_actual_corrupt_artifacts_are_rejected_before_pickle_loading(corruption, monkeypatch):
    import inference.scoring as scoring

    trusted = (ROOT / "artifacts/models").resolve()
    with tempfile.TemporaryDirectory(prefix="phase9-contract-", dir=trusted) as temporary:
        path = Path(temporary).resolve()
        assert path.is_relative_to(trusted)
        for source in MODEL.iterdir():
            if source.is_file():
                shutil.copy2(source, path / source.name)
        metadata = json.loads((path / "metadata.json").read_text())
        if corruption == "bytes":
            (path / "mlp.pt").write_bytes(b"not a model")
        elif corruption == "schema":
            metadata["feature_schema"]["names"].reverse()
        else:
            normalizer = json.loads((path / "normalizer.json").read_text())
            normalizer["means"] = [0]
            (path / "normalizer.json").write_text(json.dumps(normalizer))
            metadata["artifact_sha256"]["normalizer.json"] = hashlib.sha256(
                (path / "normalizer.json").read_bytes()
            ).hexdigest()
        (path / "metadata.json").write_text(json.dumps(metadata))
        loader = AsyncMock()
        monkeypatch.setattr(scoring.joblib, "load", loader)
        with pytest.raises(ValueError):
            Scorer(path)
        loader.assert_not_called()


@pytest.mark.parametrize("corruption", ["ordering", "values", "coverage", "quality", "timestamp"])
def test_corrupt_feature_window_is_skipped(corruption):
    now = datetime.now(UTC)
    schema = {
        "version": "test",
        "hash": "test",
        "names": ["a", "b"],
        "window_seconds": 900,
    }
    window = FeatureWindow(
        window_end=now,
        window_start=now - timedelta(seconds=900),
        schema_version="test",
        schema_hash="test",
        feature_names=["a", "b"],
        feature_values=[1, None],
        features={"a": 1, "b": None},
        quality={"metrics": {"cpu_usage_percent": {"coverage_seconds": 800}}},
    )
    assert eligible(window, {"feature_schema": schema}, now, 90)
    if corruption == "ordering":
        window.feature_names = ["b", "a"]
    elif corruption == "values":
        window.feature_values = [None, 1]
    elif corruption == "coverage":
        window.quality["metrics"]["cpu_usage_percent"]["coverage_seconds"] = 719
    elif corruption == "quality":
        window.quality = None
    else:
        window.quality["metrics"]["cpu_usage_percent"]["count"] = 1
    assert not eligible(window, {"feature_schema": schema}, now, 90)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unavailable", "invalid", "oversized", "valid"])
async def test_llm_failures_are_counted_and_facts_remain_safe(outcome):
    def handle(request):
        if outcome == "unavailable":
            raise httpx.ConnectError("private-provider-key")
        if outcome == "oversized":
            return httpx.Response(200, content=b"x" * 16385)
        return httpx.Response(
            200,
            json={
                "evidence_ids": [0] if outcome == "valid" else [999],
                "checks": ["model"],
            },
        )

    registry = CollectorRegistry()
    requests = Counter("sentinel_llm_requests", "attempts", registry=registry)
    failures = Counter("sentinel_llm_failures", "failures", registry=registry)
    facts = ["Probability", "Anomaly", "Unvalidated model"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await summarize(
            facts,
            HTTPProvider(Settings(_env_file=None, llm_base_url="http://llm.fixture"), client),
            requests,
            failures,
        )
    assert result["source"] == (
        "llm_selected_supplied_facts" if outcome == "valid" else "deterministic_fallback"
    )
    assert result["evidence"] == facts
    assert requests._value.get() == 1 and failures._value.get() == int(outcome != "valid")
    assert "private-provider-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_slack_unavailable_has_bounded_retry_and_no_secret_detail():
    payload = {
        "incident_id": "fixture",
        "forecast_valid_until": "2026-09-27T00:00:00Z",
        "model_validation_warning": "unvalidated",
        "host": "fixture",
        "failure_probability": 0.9,
        "anomaly_score": 0.1,
        "horizon_seconds": 600,
        "summary": {"summary": "fixture"},
        "top_drivers": [],
        "dashboard_link": "http://127.0.0.1:5173",
        "model_version": "fixture",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        status, error = await deliver(
            client, "https://slack.fixture/private", "slack", "fixture", payload
        )
    assert status == "retry" and "private" not in str(error)


def test_required_inference_metrics_are_registered_without_dynamic_labels():
    registry = CollectorRegistry()
    Worker(Settings(_env_file=None), AsyncMock(), AsyncMock(), AsyncMock(), registry)
    exported = generate_latest(registry).decode()
    for name in [
        "sentinel_predictions_total",
        "sentinel_high_risk_predictions_total",
        "sentinel_alerts_total",
        "sentinel_alert_failures_total",
        "sentinel_llm_requests_total",
        "sentinel_llm_failures_total",
        "sentinel_inference_duration_seconds",
        "sentinel_worker_last_success_timestamp",
    ]:
        assert name in exported
