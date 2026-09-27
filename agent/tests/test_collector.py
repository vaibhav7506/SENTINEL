import pytest
from prometheus_client import CollectorRegistry, generate_latest
from sentinel_agent.collector import HostCollector, counter_rates
from sentinel_agent.config import Settings


def test_rates_use_elapsed_time():
    assert counter_rates((10.0, {"read": 100}), 12.0, {"read": 500}) == {"read": 200.0}


@pytest.mark.parametrize(
    "previous,now,counters",
    [
        (None, 1.0, {"read": 500}),
        ((2.0, {"read": 100}), 2.0, {"read": 500}),
        ((2.0, {"read": 500}), 3.0, {"read": 100}),
    ],
)
def test_first_sample_and_counter_resets_are_absent(previous, now, counters):
    assert counter_rates(previous, now, counters) == {}


def test_real_snapshot_and_bounded_labels():
    collector = HostCollector(Settings(_env_file=None, agent_disk_path="."))
    collector.sample()
    families = list(collector.collect())
    names = {item.name for item in families}
    assert "sentinel_host_memory_capacity_bytes" in names
    assert "sentinel_host_cpu_usage_percent" in names
    assert "sentinel_host_disk_read_bytes_per_second" not in names
    for family in families:
        for sample in family.samples:
            assert set(sample.labels) == {"host_id", "host_name", "environment"}
    collector.sample()
    assert list(collector.collect())


def test_missing_optional_counters_and_load(monkeypatch):
    collector = HostCollector(Settings(_env_file=None, agent_disk_path="."))
    monkeypatch.setattr("psutil.disk_io_counters", lambda: None)
    monkeypatch.setattr("psutil.net_io_counters", lambda: None)

    def unsupported():
        raise NotImplementedError()

    monkeypatch.setattr("psutil.getloadavg", unsupported)
    collector.sample()
    names = {metric.name for metric in collector.collect()}
    assert "sentinel_host_memory_usage_percent" in names
    assert "sentinel_host_cpu_load_1m" not in names
    assert "sentinel_host_network_receive_bytes_per_second" not in names


def test_failure_discards_stale_snapshot(monkeypatch):
    collector = HostCollector(Settings(_env_file=None, agent_disk_path="."))
    collector.sample()

    def fail():
        raise OSError("private-system-details")

    monkeypatch.setattr("psutil.virtual_memory", fail)
    collector.sample()
    registry = CollectorRegistry()
    registry.register(collector)
    output = generate_latest(registry).decode()
    assert "sentinel_host_sample_success" in output
    assert "memory_usage" not in output
    assert "private-system-details" not in output


@pytest.mark.parametrize(
    "changes", [{"agent_port": 0}, {"host_id": ""}, {"agent_sample_seconds": 0}]
)
def test_configuration_rejects_invalid_values(changes):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **changes)


@pytest.mark.parametrize("name", ["disk_io_counters", "net_io_counters"])
def test_unsupported_optional_io_preserves_core_snapshot(monkeypatch, name):
    collector = HostCollector(Settings(_env_file=None, agent_disk_path="."))

    def unsupported():
        raise NotImplementedError()

    monkeypatch.setattr("psutil." + name, unsupported)
    collector.sample()
    assert "sentinel_host_memory_usage_percent" in {metric.name for metric in collector.collect()}
