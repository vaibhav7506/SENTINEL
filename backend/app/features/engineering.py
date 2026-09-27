"""Pure causal transformations. No labels, imputation, or model state."""

import math
import statistics
from dataclasses import dataclass

from app.features.schema import APP_METRICS, HOST_METRICS, STATS, FeatureSchema

Sample = tuple[float, float]


@dataclass(frozen=True)
class FeatureResult:
    features: dict[str, float | None]
    values: list[float | None]
    quality: dict[str, object]


def clean_samples(samples: list[Sample], end: float, window: int) -> list[Sample]:
    unique: dict[float, float] = {}
    for timestamp, value in samples:
        if not math.isfinite(timestamp) or not math.isfinite(value):
            continue
        if end - window < timestamp <= end:
            if timestamp in unique and unique[timestamp] != value:
                raise ValueError("Conflicting samples at the same timestamp")
            unique[timestamp] = value
    return sorted(unique.items())


def statistics_for(
    samples: list[Sample], end: float, schema: FeatureSchema
) -> dict[str, float | None]:
    result: dict[str, float | None] = dict.fromkeys(STATS)
    if not samples:
        return result
    values = [value for _, value in samples]
    rolling = [
        value for timestamp, value in samples if timestamp > end - schema.variance_window_seconds
    ]
    ewma = values[0]
    for (previous, _), (timestamp, value) in zip(samples, samples[1:], strict=False):
        alpha = -math.expm1(-math.log(2) * (timestamp - previous) / schema.ewma_half_life_seconds)
        ewma += alpha * (value - ewma)
    result.update(
        latest=values[-1],
        mean=statistics.fmean(values),
        median=statistics.median(values),
        std=statistics.pstdev(values),
        min=min(values),
        max=max(values),
        ewma=ewma,
        rolling_variance=statistics.pvariance(rolling) if rolling else None,
    )
    if len(samples) > 1:
        delta = values[-1] - values[0]
        times = [timestamp - samples[0][0] for timestamp, _ in samples]
        tmean, vmean = statistics.fmean(times), statistics.fmean(values)
        slope = sum((t - tmean) * (v - vmean) for t, v in zip(times, values, strict=True)) / sum(
            (t - tmean) ** 2 for t in times
        )
        result.update(
            delta=delta,
            percentage_change=100 * delta / abs(values[0]) if values[0] else None,
            linear_trend=slope,
            rate_of_change=delta / times[-1],
        )
    return {
        name: value if value is None or math.isfinite(value) else None
        for name, value in result.items()
    }


def generate_features(
    series: dict[str, list[Sample]], end: float, schema: FeatureSchema
) -> FeatureResult:
    if not math.isfinite(end):
        raise ValueError("Window end must be finite")
    features: dict[str, float | None] = dict.fromkeys(schema.names)
    quality: dict[str, object] = {}
    for metric in HOST_METRICS + APP_METRICS:
        samples = clean_samples(series.get(metric, []), end, schema.window_seconds)
        for stat, value in statistics_for(samples, end, schema).items():
            features[f"{metric}.{stat}"] = value
        quality[metric] = {
            "count": len(samples),
            "first_timestamp": samples[0][0] if samples else None,
            "last_timestamp": samples[-1][0] if samples else None,
            "coverage_seconds": samples[-1][0] - samples[0][0] if samples else 0,
            "age_seconds": end - samples[-1][0] if samples else None,
        }

    def product(output: str, *inputs: str) -> None:
        values = [features[name] for name in inputs]
        if all(value is not None for value in values):
            value = math.prod(value for value in values if value is not None)
            features[output] = value if math.isfinite(value) else None

    product(
        "cpu_trend_times_latency_trend",
        "cpu_usage_percent.linear_trend",
        "latency_p95_seconds.linear_trend",
    )
    product(
        "memory_trend_times_latency_trend",
        "memory_usage_percent.linear_trend",
        "latency_p95_seconds.linear_trend",
    )
    product("error_trend_times_latency", "error_rate.linear_trend", "latency_p95_seconds.latest")
    for output, first, second, companion in (
        (
            "disk_throughput_times_latency",
            "disk_read_bytes_per_second.latest",
            "disk_write_bytes_per_second.latest",
            "latency_p95_seconds.latest",
        ),
        (
            "network_change_times_error_rate",
            "network_receive_bytes_per_second.rate_of_change",
            "network_transmit_bytes_per_second.rate_of_change",
            "error_rate.latest",
        ),
    ):
        a, b, c = (features[name] for name in (first, second, companion))
        if a is not None and b is not None and c is not None:
            value = (a + b) * c
            features[output] = value if math.isfinite(value) else None
    return FeatureResult(features, list(features.values()), quality)
