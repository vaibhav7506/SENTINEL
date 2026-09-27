import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from prometheus_client import CollectorRegistry

from app.core.config import Settings
from app.features.engineering import clean_samples, generate_features
from app.features.prometheus import HostSeries, PrometheusSource, matrix
from app.features.schema import FeatureSchema
from app.workers.features import FeatureWorker


def test_irregular_time_statistics_and_population_variance():
    result = generate_features(
        {"cpu_usage_percent": [(100, 10), (110, 30), (130, 70)]}, 130, FeatureSchema()
    )
    f = result.features
    assert f["cpu_usage_percent.linear_trend"] == pytest.approx(2)
    assert f["cpu_usage_percent.rate_of_change"] == pytest.approx(2)
    assert f["cpu_usage_percent.delta"] == 60
    assert f["cpu_usage_percent.percentage_change"] == 600
    assert f["cpu_usage_percent.mean"] == pytest.approx(110 / 3)
    assert f["cpu_usage_percent.median"] == 30
    assert f["cpu_usage_percent.std"] == pytest.approx(math.sqrt(5600 / 9))
    assert f["cpu_usage_percent.rolling_variance"] == pytest.approx(5600 / 9)


def test_time_aware_ewma_and_recent_variance():
    result = generate_features(
        {"cpu_usage_percent": [(1, 0), (301, 100), (361, 200)]}, 361, FeatureSchema()
    )
    assert result.features["cpu_usage_percent.ewma"] == pytest.approx(148.4375)
    assert result.features["cpu_usage_percent.rolling_variance"] == 2500
    assert result.features["cpu_usage_percent.percentage_change"] is None


def test_feature_time_boundary_and_future_leakage():
    schema = FeatureSchema(window_seconds=60)
    past = {"cpu_usage_percent": [(40, 9999), (41, 2), (70, 4), (100, 6)]}
    expected = generate_features(past, 100, schema)
    actual = generate_features(
        {"cpu_usage_percent": past["cpu_usage_percent"] + [(101, 1e12), (200, -1e12)]}, 100, schema
    )
    assert actual == expected
    assert actual.features["cpu_usage_percent.latest"] == 6
    assert actual.quality["cpu_usage_percent"]["count"] == 3


def test_sample_cleanup_ordering_duplicates_and_nonfinite():
    assert clean_samples(
        [(10, 2), (9, 1), (10, 2), (11, float("nan")), (float("inf"), 5)], 20, 60
    ) == [(9, 1), (10, 2)]
    with pytest.raises(ValueError, match="Conflicting"):
        clean_samples([(10, 1), (10, 2)], 20, 60)


def test_constant_single_missing_and_schema_order():
    schema = FeatureSchema()
    result = generate_features(
        {"cpu_usage_percent": [(10, 3), (20, 3)], "memory_usage_percent": [(20, 0)]}, 20, schema
    )
    assert tuple(result.features) == schema.names
    assert len(set(schema.names)) == len(result.values) == 209
    assert result.values == [result.features[name] for name in schema.names]
    assert result.features["cpu_usage_percent.std"] == 0
    assert result.features["cpu_usage_percent.linear_trend"] == 0
    assert result.features["memory_usage_percent.delta"] is None
    assert result.features["latency_p95_seconds.mean"] is None
    assert result.features["cpu_trend_times_latency_trend"] is None
    assert schema.digest == FeatureSchema().digest
    assert schema.digest != FeatureSchema(window_seconds=600).digest
    assert schema.version != FeatureSchema(ewma_half_life_seconds=30).version
    with pytest.raises(ValueError):
        FeatureSchema(window_seconds=0)


def test_cross_signal_products_require_all_inputs():
    series = {
        name: [(10, a), (20, b)]
        for name, a, b in (
            ("cpu_usage_percent", 10, 30),
            ("memory_usage_percent", 40, 50),
            ("latency_p95_seconds", 1, 2),
            ("error_rate", 0, 1),
            ("disk_read_bytes_per_second", 100, 200),
            ("disk_write_bytes_per_second", 10, 20),
            ("network_receive_bytes_per_second", 100, 200),
            ("network_transmit_bytes_per_second", 10, 20),
        )
    }
    f = generate_features(series, 20, FeatureSchema()).features
    assert f["cpu_trend_times_latency_trend"] == pytest.approx(0.2)
    assert f["memory_trend_times_latency_trend"] == pytest.approx(0.1)
    assert f["error_trend_times_latency"] == pytest.approx(0.2)
    assert f["disk_throughput_times_latency"] == 440
    assert f["network_change_times_error_rate"] == 11


def response(items):
    return httpx.Response(
        200, json={"status": "success", "data": {"resultType": "matrix", "result": items}}
    )


def host(values, host_id="host-a"):
    return {
        "metric": {"host_id": host_id, "host_name": "Host A", "environment": "test"},
        "values": values,
    }


async def test_source_bounds_future_samples_optional_failures_and_mapping():
    queries = []

    def handle(request):
        queries.append(request)
        query = request.url.params["query"]
        if request.url.path.endswith("query_range"):
            assert 'job="test-service"' in query
            assert float(request.url.params["end"]) == 1000
            assert float(request.url.params["start"]) == 100
            if "histogram_quantile" in query:
                return httpx.Response(503)
            return response([])
        assert float(request.url.params["time"]) == 1000
        assert query.endswith("[900s]")
        if "cpu_usage_percent" in query:
            return response([host([[900, "1"], [990, "2"], [1001, "9999"]])])
        if "memory_usage_percent" in query:
            return response([host([[900, "20"], [990, "30"]])])
        return response([])

    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(handle)
    ) as client:
        result = await PrometheusSource(client, 900, {"host-a": "test-service"}).collect(1000, 90)
    assert result["host-a"].series["cpu_usage_percent"] == [(900, 1), (990, 2)]
    assert result["host-a"].series["memory_usage_percent"][-1] == (990, 30)
    assert result["host-a"].query_errors == ["latency_p95_seconds"]
    features = generate_features(result["host-a"].series, 1000, FeatureSchema())
    assert features.features["cpu_usage_percent.latest"] == 2
    assert features.features["latency_p95_seconds.latest"] is None
    assert len(queries) == 17


async def test_source_skips_stale_hosts_and_unmapped_services():
    queries = []

    def handle(request):
        queries.append(request)
        if "cpu_usage_percent" in request.url.params["query"]:
            return response([host([[800, "10"]]), host([[999, "20"]], "host-b")])
        return response([])

    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(handle)
    ) as client:
        result = await PrometheusSource(client, 900, {}).collect(1000, 90)
    assert list(result) == ["host-b"]
    assert all(request.url.path.endswith("/query") for request in queries)


async def test_source_rejects_duplicate_exporters():
    def handle(request):
        return response([host([[999, "1"]]), host([[999, "2"]])])

    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ValueError, match="Ambiguous"):
            await PrometheusSource(client, 900, {}).collect(1000, 90)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "success", "data": None},
        {"status": "error"},
        {"status": "success", "data": {"resultType": "vector", "result": []}},
    ],
)
def test_bad_prometheus_contract(payload):
    with pytest.raises(ValueError):
        matrix(payload)


async def test_worker_counts_only_committed_windows_and_isolates_host_failures():
    source = AsyncMock()
    source.collect.return_value = {
        key: HostSeries(key, "test", {"cpu_usage_percent": [(990, 5)]}) for key in ("a", "b", "c")
    }
    worker = FeatureWorker(Settings(_env_file=None), source, AsyncMock(), CollectorRegistry())
    with patch(
        "app.workers.features.persist_window",
        new=AsyncMock(side_effect=[RuntimeError(), True, False]),
    ) as persist:
        assert await worker.cycle(datetime.fromtimestamp(1000, UTC)) == 1
        assert persist.await_count == 3
    assert worker.windows._value.get() == 1
    assert worker.errors._value.get() == 1
    assert worker.success._value.get() == 0


async def test_worker_failed_discovery_creates_no_windows():
    source = AsyncMock()
    source.collect.side_effect = httpx.ConnectError("private-url")
    worker = FeatureWorker(Settings(_env_file=None), source, AsyncMock(), CollectorRegistry())
    with patch("app.workers.features.persist_window", new=AsyncMock()) as persist:
        assert await worker.cycle(datetime.fromtimestamp(1000, UTC)) == 0
        persist.assert_not_awaited()
    assert worker.errors._value.get() == 1


async def test_missing_prometheus_data_and_disappearing_host_create_no_fake_window():
    visible = [True]

    def handle(request):
        if visible[0] and "cpu_usage_percent" in request.url.params["query"]:
            return response([host([[999, "20"]])])
        return response([])

    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(handle)
    ) as client:
        source = PrometheusSource(client, 900, {})
        assert list(await source.collect(1000, 90)) == ["host-a"]
        visible[0] = False
        assert await source.collect(1000, 90) == {}
        worker = FeatureWorker(Settings(_env_file=None), source, AsyncMock(), CollectorRegistry())
        with patch("app.workers.features.persist_window", new=AsyncMock()) as persist:
            assert await worker.cycle(datetime.fromtimestamp(1000, UTC)) == 0
            persist.assert_not_awaited()
        assert worker.worker_success._value.get() == 0


async def test_prometheus_http_failure_does_not_become_a_healthy_sample():
    async with httpx.AsyncClient(
        base_url="http://prometheus", transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await PrometheusSource(client, 900, {}).collect(1000, 90)
