from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models import Host
from app.services.chaos import ExperimentRequest, enforce_policy
from app.workers.observer import classify
from training.labels import Event, Probe, grouped_split, label_window

BASE = datetime(2026, 1, 1, tzinfo=UTC)
CRITERIA = {"latency_slo_seconds": 0.5, "http_error_status_min": 500, "probe": "/work?units=100000"}


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def event(onset=600, recovered=620, confirmed=606, identifier="event"):
    return Event(
        identifier,
        "host",
        "experiment",
        "latency_slo_breach",
        at(onset),
        at(confirmed),
        at(recovered) if recovered is not None else None,
    )


def healthy_probes():
    return [Probe(at(second), True, CRITERIA) for second in range(0, 4000, 2)]


def label(end=100, events=None, probes=None, as_of=3000, horizon=600):
    return label_window(
        "host",
        at(end),
        horizon,
        900,
        events or [],
        healthy_probes() if probes is None else probes,
        CRITERIA,
        at(as_of),
    )


def test_failure_horizon_boundary_and_known_confirmation():
    assert label(end=0, events=[event()]).value == 1
    assert label(end=0, events=[event(onset=601)]).value == 0
    assert label(end=600, events=[event()]).reason == "active_failure_or_recovery"
    assert label(end=0, events=[event()], as_of=603).value == 0
    breached = healthy_probes()
    breached[300] = Probe(at(600), False, CRITERIA)
    assert label(end=0, events=[event()], probes=breached, as_of=603).value is None
    assert label(end=0, events=[event()], as_of=607).event_id == "event"


def test_real_failure_labels_do_not_require_assumed_healthy_coverage():
    result = label(events=[event()], probes=[])
    assert result.value == 1
    assert result.experiment_id == "experiment"


@pytest.mark.parametrize("end", [600, 610, 620, 1000, 1520])
def test_active_failure_and_recovery_windows_are_excluded(end):
    assert label(end=end, events=[event()]).reason == "active_failure_or_recovery"
    assert label(end=end, events=[event(recovered=None)]).value is None


def test_after_recovery_requires_full_future_coverage():
    assert label(end=1522, events=[event()]).value == 0
    assert label(as_of=699).reason == "right_censored"


def test_negative_requires_actual_complete_healthy_probes():
    assert label().value == 0
    assert label(probes=[]).reason == "incomplete_observation_coverage"
    gap = [probe for probe in healthy_probes() if not at(200) <= probe.at <= at(230)]
    assert label(probes=gap).reason == "observation_gap"
    changed = healthy_probes()
    changed[100] = Probe(at(200), True, None)
    assert label(probes=changed).reason == "unknown_or_changed_criteria"
    breached = healthy_probes()
    breached[100] = Probe(at(200), False, CRITERIA)
    assert label(probes=breached).reason == "unconfirmed_degradation"


def test_nearest_failure_is_primary_and_other_hosts_do_not_contaminate():
    later = event(onset=650, confirmed=656, recovered=670, identifier="later")
    assert label(events=[later, event()]).event_id == "event"
    other = Event("other", "different-host", None, "http_error", at(500), at(506), at(520))
    assert label(events=[other]).value == 0


def row(end, start=None):
    return {
        "host_id": "host",
        "window_start": at(end - 900 if start is None else start).isoformat(),
        "window_end": at(end).isoformat(),
    }


def test_overlapping_experiments_and_shared_windows_stay_in_one_split():
    rows = [row(1900), row(2800), row(3700)]
    intervals = [("first", "host", at(2000), at(2010)), ("second", "host", at(3800), at(3810))]
    assigned, report = grouped_split(rows, intervals, 600, 60)
    assert len({r["group_id"] for r in assigned}) == 1
    assert all(r["split"] == "train" for r in assigned)
    assert report["status"] == "insufficient_independent_groups"


def test_chronological_groups_and_purge_remove_overlapping_feature_horizons():
    intervals = [("first", "host", at(2000), at(2010)), ("second", "host", at(10000), at(10010))]
    assigned, report = grouped_split(
        [row(1800), row(1900), row(9800), row(9900)], intervals, 600, 900
    )
    assert report["status"] == "available"
    first = [r for r in assigned if "first" in r["experiment_ids"]]
    second = [r for r in assigned if "second" in r["experiment_ids"]]
    assert {r["split"] for r in first} == {"train"}
    assert {r["split"] for r in second} == {"test"}
    # Unknown healthy groups must also be purged if their inputs overlap held-out data.
    assigned, report = grouped_split([row(86390), row(86410)], [], 600, 900)
    assert report["group_count"] == 2
    assert report["purged_samples"] == 1
    assert len(assigned) == 1
    assert assigned[0]["split"] == "test"


def allowed_settings(**changes):
    return Settings(
        _env_file=None,
        chaos_enabled=changes.get("environment") != "production",
        api_read_token="test-only-read-token-12345678901234567890",
        postgres_sslmode="require",
        chaos_allowed_environments=["development"],
        chaos_allowed_namespaces=["sentinel-demo"],
        chaos_allowed_targets=["demo-service"],
        chaos_control_token="x" * 40,
        **changes,
    )


def host(environment="development", service="demo-service"):
    return Host(
        id="docker-vm",
        name="Docker VM",
        environment=environment,
        service_job=service,
        first_seen=BASE,
        last_seen=BASE,
    )


def test_chaos_policy_requires_flag_all_allowlists_and_matching_identity():
    request = ExperimentRequest(failure_type="http_error")
    enforce_policy(allowed_settings(), request, host())
    for settings in (
        Settings(_env_file=None),
        allowed_settings().model_copy(update={"chaos_enabled": False}),
        allowed_settings().model_copy(update={"chaos_allowed_namespaces": []}),
        allowed_settings().model_copy(update={"chaos_allowed_targets": []}),
        allowed_settings().model_copy(update={"chaos_allowed_environments": []}),
        allowed_settings(environment="production").model_copy(
            update={"chaos_allowed_environments": ["production"]}
        ),
    ):
        with pytest.raises(HTTPException):
            enforce_policy(settings, request, host())
    for target in (None, host(environment="production"), host(service="arbitrary-service")):
        with pytest.raises(HTTPException):
            enforce_policy(allowed_settings(), request, target)


@pytest.mark.parametrize(
    "changes",
    [
        {"failure_type": "cpu_saturation"},
        {"target": "external"},
        {"namespace": "production"},
        {"duration_seconds": 121},
        {"latency_ms": 2001},
    ],
)
def test_arbitrary_targets_and_unbounded_faults_are_rejected(changes):
    with pytest.raises(ValidationError):
        ExperimentRequest(**({"failure_type": "http_error"} | changes))


@pytest.mark.parametrize(
    "status,duration,expected",
    [
        (200, 0.1, "healthy"),
        (200, 0.6, "latency_slo_breach"),
        (503, 0.01, "http_error_slo_breach"),
        (None, 3, "service_unavailable"),
        (404, 0.01, "unexpected_response"),
    ],
)
def test_objective_probe_outcomes(status, duration, expected):
    assert classify(status, duration, 0.5) == expected


async def test_disabled_or_production_api_denies_before_database_access():
    for settings in (
        allowed_settings().model_copy(update={"chaos_enabled": False}),
        allowed_settings(environment="production").model_copy(
            update={"chaos_allowed_environments": ["production"]}
        ),
    ):
        application = create_app(settings)
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://test"
            ) as client,
        ):
            assert (
                await client.post("/chaos/experiments", json={"failure_type": "http_error"})
            ).status_code == 401
            response = await client.post(
                "/chaos/experiments",
                json={"failure_type": "http_error"},
                headers={"Authorization": "Bearer " + "x" * 40},
            )
            assert response.status_code == 403
