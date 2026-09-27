"""Shared, immutable feature contract for future training and inference."""

import hashlib
import json
from dataclasses import dataclass

HOST_METRICS = (
    "cpu_usage_percent",
    "memory_usage_percent",
    "memory_available_bytes",
    "swap_usage_percent",
    "disk_usage_percent",
    "disk_read_bytes_per_second",
    "disk_write_bytes_per_second",
    "network_receive_bytes_per_second",
    "network_transmit_bytes_per_second",
    "process_count",
    "uptime_seconds",
    "cpu_load_1m",
    "cpu_load_5m",
    "cpu_load_15m",
)
APP_METRICS = ("request_rate", "error_rate", "latency_p95_seconds")
STATS = (
    "latest",
    "mean",
    "median",
    "std",
    "min",
    "max",
    "delta",
    "percentage_change",
    "linear_trend",
    "ewma",
    "rolling_variance",
    "rate_of_change",
)
CROSS_SIGNALS = (
    "cpu_trend_times_latency_trend",
    "memory_trend_times_latency_trend",
    "error_trend_times_latency",
    "disk_throughput_times_latency",
    "network_change_times_error_rate",
)


@dataclass(frozen=True)
class FeatureSchema:
    window_seconds: int = 900
    ewma_half_life_seconds: int = 60
    variance_window_seconds: int = 300

    def __post_init__(self) -> None:
        if min(self.window_seconds, self.ewma_half_life_seconds, self.variance_window_seconds) <= 0:
            raise ValueError("Schema durations must be positive")

    @property
    def names(self) -> tuple[str, ...]:
        return (
            tuple(f"{metric}.{stat}" for metric in HOST_METRICS + APP_METRICS for stat in STATS)
            + CROSS_SIGNALS
        )

    @property
    def version(self) -> str:
        return (
            f"v1-w{self.window_seconds}-e{self.ewma_half_life_seconds}"
            f"-r{self.variance_window_seconds}"
        )

    @property
    def digest(self) -> str:
        contract = {
            "version": self.version,
            "names": self.names,
            "bounds": "(end-window,end]",
            "missing": None,
            "std": "population",
            "trend": "OLS per second",
            "rate": "endpoint delta per second",
            "percentage_change": "100*delta/abs(first); undefined at zero",
            "ewma": "time-aware half-life, seeded with first observation",
            "variance": "population of samples after end-rolling-window",
            "cross": "products of named statistics; disk throughput is not utilization",
        }
        return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
