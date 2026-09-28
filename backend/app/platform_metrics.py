"""Fixed aggregate Redis metrics; never export customer identifiers or metric values."""

from typing import Any

NAMES = {
    "tasks_started": "counter",
    "tasks_succeeded": "counter",
    "tasks_failed": "counter",
    "task_duration_sum": "counter",
    "worker_last_success": "gauge",
    "predictions": "counter",
    "sentinel_high_risk_predictions_total": "counter",
    "sentinel_alert_deliveries_total": "counter",
    "sentinel_alert_failures_total": "counter",
    "sentinel_llm_failures_total": "counter",
    "sentinel_llm_requests_total": "counter",
}
BUCKETS = (
    "0.005",
    "0.01",
    "0.025",
    "0.05",
    "0.075",
    "0.1",
    "0.25",
    "0.5",
    "0.75",
    "1.0",
    "2.5",
    "5.0",
    "7.5",
    "10.0",
    "+Inf",
)


def snapshot(registry: Any) -> dict[str, float]:
    result = {}
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name in NAMES:
                result[sample.name] = sample.value
            elif sample.name in {
                "sentinel_inference_duration_seconds_sum",
                "sentinel_inference_duration_seconds_count",
            }:
                result[sample.name] = sample.value
            elif (
                sample.name == "sentinel_inference_duration_seconds_bucket"
                and sample.labels.get("le") in BUCKETS
            ):
                result[sample.name + ":" + sample.labels["le"]] = sample.value
    return result


async def render(redis: Any) -> bytes:
    try:
        values = await redis.mget(["sentinel:platform:" + name for name in NAMES])
        queue_depth = await redis.llen("sentinel")
        lines = [
            "# TYPE sentinel_platform_redis_up gauge",
            "sentinel_platform_redis_up 1",
            "# TYPE sentinel_platform_queue_depth gauge",
            f"sentinel_platform_queue_depth {queue_depth}",
        ]
        for (name, kind), value in zip(NAMES.items(), values, strict=True):
            output = "sentinel_platform_" + name.removeprefix("sentinel_")
            if kind == "counter" and not output.endswith("_total"):
                output += "_total"
            lines.extend([f"# TYPE {output} {kind}", f"{output} {float(value or 0)}"])
        base = "sentinel_inference_duration_seconds"
        keys = [base + "_bucket:" + le for le in BUCKETS] + [base + "_sum", base + "_count"]
        histogram = await redis.mget(["sentinel:platform:" + key for key in keys])
        lines.append("# TYPE sentinel_platform_prediction_duration_seconds histogram")
        for le, value in zip(BUCKETS, histogram, strict=False):
            lines.append(
                f'sentinel_platform_prediction_duration_seconds_bucket{{le="{le}"}} '
                f"{float(value or 0)}"
            )
        lines.extend(
            [
                f"sentinel_platform_prediction_duration_seconds_sum {float(histogram[-2] or 0)}",
                f"sentinel_platform_prediction_duration_seconds_count {float(histogram[-1] or 0)}",
            ]
        )
        return ("\n".join(lines) + "\n").encode()
    except Exception:
        return b"# TYPE sentinel_platform_redis_up gauge\nsentinel_platform_redis_up 0\n"
