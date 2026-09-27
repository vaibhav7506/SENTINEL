import pytest
from pathlib import Path
import tempfile

from sentinel.config import (
    ServiceConfig,
    Provider,
    PolicyConfig,
    load_services_config,
    load_policy_config,
    Settings,
)
from sentinel.storage import Storage, HealthCheck, Incident, DriftEvent, Prediction
from datetime import datetime


class TestServiceConfig:
    def test_valid_service_config(self):
        config = ServiceConfig(
            name="test-service",
            url="https://example.com/health",
            expected_status=200,
            expected_latency_ms=500,
            provider=Provider.CLOUDFLARE,
            provider_resource_id="test-id",
            env_vars_expected={"KEY": "value"},
        )
        assert config.name == "test-service"
        assert config.provider == Provider.CLOUDFLARE

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError):
            ServiceConfig(
                name="test",
                url="not-a-url",
                provider=Provider.CLOUDFLARE,
                provider_resource_id="id",
            )

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError):
            ServiceConfig(
                name="test",
                url="https://example.com",
                expected_status=999,
                provider=Provider.CLOUDFLARE,
                provider_resource_id="id",
            )


class TestLoadServicesConfig:
    def test_load_valid_services_yaml(self):
        yaml_content = """
services:
  - name: "svc1"
    url: "https://svc1.example.com"
    provider: "cloudflare"
    provider_resource_id: "id1"
  - name: "svc2"
    url: "https://svc2.example.com"
    provider: "vercel"
    provider_resource_id: "id2"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            services = load_services_config(f.name)

        assert len(services) == 2
        assert services[0].name == "svc1"
        assert services[1].name == "svc2"

    def test_missing_services_key_raises(self):
        yaml_content = "other_key: []"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            with pytest.raises(ValueError, match="must contain a 'services' key"):
                load_services_config(f.name)

    def test_duplicate_names_raises(self):
        yaml_content = """
services:
  - name: "svc1"
    url: "https://svc1.example.com"
    provider: "cloudflare"
    provider_resource_id: "id1"
  - name: "svc1"
    url: "https://svc2.example.com"
    provider: "vercel"
    provider_resource_id: "id2"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            with pytest.raises(ValueError, match="unique"):
                load_services_config(f.name)


class TestLoadPolicyConfig:
    def test_load_valid_policy(self):
        yaml_content = """
policy:
  retry:
    max_attempts: 5
  circuit_breaker:
    failure_threshold: 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            policy = load_policy_config(f.name)

        assert policy.retry.max_attempts == 5
        assert policy.circuit_breaker.failure_threshold == 10

    def test_defaults_when_missing(self):
        yaml_content = "policy: {}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            policy = load_policy_config(f.name)

        assert policy.retry.max_attempts == 3
        assert policy.polling.interval_seconds == 300


class TestStorage:
    @pytest.fixture
    def storage(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        storage = Storage(db_path)
        yield storage
        storage.close()
        Path(db_path).unlink(missing_ok=True)

    def test_insert_and_get_health_check(self, storage):
        check = HealthCheck(
            id=None,
            service_name="test-svc",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            status_code=200,
            latency_ms=100.0,
            success=True,
        )
        inserted_id = storage.insert_health_check(check)
        assert inserted_id > 0

        checks = storage.get_recent_health_checks("test-svc", limit=10)
        assert len(checks) == 1
        assert checks[0].service_name == "test-svc"
        assert checks[0].status_code == 200
        assert checks[0].success is True

    def test_insert_and_get_incident(self, storage):
        incident = Incident(
            id=None,
            service_name="test-svc",
            started_at=datetime(2024, 1, 1, 12, 0, 0),
            resolved_at=None,
            failure_type="timeout",
            remediation_action=None,
            remediation_success=None,
        )
        inserted_id = storage.insert_incident(incident)
        assert inserted_id > 0

        open_incident = storage.get_open_incident("test-svc")
        assert open_incident is not None
        assert open_incident.failure_type == "timeout"

        storage.update_incident_resolution(
            inserted_id, datetime(2024, 1, 1, 12, 5, 0), "retry", True
        )
        open_incident = storage.get_open_incident("test-svc")
        assert open_incident is None

        incidents = storage.get_incidents("test-svc")
        assert len(incidents) == 1
        assert incidents[0].remediation_action == "retry"
        assert incidents[0].remediation_success is True

    def test_insert_and_get_drift_event(self, storage):
        event = DriftEvent(
            id=None,
            service_name="test-svc",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            diff_json='{"missing": ["KEY1"]}',
        )
        inserted_id = storage.insert_drift_event(event)
        assert inserted_id > 0

        events = storage.get_drift_events("test-svc")
        assert len(events) == 1
        assert events[0].diff_json == '{"missing": ["KEY1"]}'

    def test_insert_and_get_prediction(self, storage):
        prediction = Prediction(
            id=None,
            service_name="test-svc",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            failure_probability=0.75,
            model_version="v1",
        )
        inserted_id = storage.insert_prediction(prediction)
        assert inserted_id > 0

        latest = storage.get_latest_prediction("test-svc")
        assert latest is not None
        assert latest.failure_probability == 0.75
        assert latest.model_version == "v1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])