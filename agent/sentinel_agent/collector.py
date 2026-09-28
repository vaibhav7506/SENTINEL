import logging
import sys
import threading
import time
from collections.abc import Iterator

import psutil
from prometheus_client.core import GaugeMetricFamily

from sentinel_agent.config import Settings

logger = logging.getLogger(__name__)

# All I/O is aggregated. Devices, interfaces, PIDs and paths never become labels.
METRICS = {
    "cpu_usage_percent": "CPU busy percentage between sampler ticks",
    "cpu_count": "Logical CPU count",
    "cpu_load_1m": "One minute CPU load average, when supported",
    "cpu_load_5m": "Five minute CPU load average, when supported",
    "cpu_load_15m": "Fifteen minute CPU load average, when supported",
    "memory_usage_percent": "Used memory percentage",
    "memory_available_bytes": "Available memory bytes",
    "memory_capacity_bytes": "Total memory bytes",
    "swap_usage_percent": "Used swap percentage",
    "disk_usage_percent": "Used space percentage on configured filesystem",
    "disk_capacity_bytes": "Configured filesystem capacity bytes",
    "disk_read_bytes_per_second": "Aggregate disk read throughput",
    "disk_write_bytes_per_second": "Aggregate disk write throughput",
    "network_receive_bytes_per_second": "Aggregate network receive throughput",
    "network_transmit_bytes_per_second": "Aggregate network transmit throughput",
    "process_count": "Processes visible in the configured process namespace",
    "uptime_seconds": "Seconds since system boot",
    "sample_timestamp_seconds": "Unix time of last complete host snapshot",
    "sample_success": "Whether the latest sampling attempt succeeded",
}


def counter_rates(
    previous: tuple[float, dict[str, int]] | None, now: float, counters: dict[str, int]
) -> dict[str, float]:
    if previous is None or now <= previous[0]:
        return {}
    elapsed = now - previous[0]
    return {
        key: (value - previous[1][key]) / elapsed
        for key, value in counters.items()
        if key in previous[1] and value >= previous[1][key]
    }


class HostCollector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._snapshot: dict[str, float] = {"sample_success": 0}
        self._previous: tuple[float, dict[str, int]] | None = None
        if settings.agent_procfs_path:
            if sys.platform != "linux":
                raise ValueError("Custom procfs is supported only on Linux")
            # The public Linux setting is absent from Windows type stubs.
            vars(psutil)["PROCFS_PATH"] = settings.agent_procfs_path
        psutil.cpu_percent(interval=None)  # Prime the delta; do not publish a fake first zero.

    def sample(self) -> None:
        try:
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(self.settings.agent_disk_path)
            current: dict[str, float] = {
                "cpu_usage_percent": psutil.cpu_percent(interval=None),
                "memory_usage_percent": memory.percent,
                "memory_available_bytes": float(memory.available),
                "memory_capacity_bytes": float(memory.total),
                "swap_usage_percent": psutil.swap_memory().percent,
                "disk_usage_percent": disk.percent,
                "disk_capacity_bytes": float(disk.total),
                "process_count": float(len(psutil.pids())),
                "uptime_seconds": max(0, time.time() - psutil.boot_time()),
                "sample_timestamp_seconds": time.time(),
                "sample_success": 1,
            }
            count = psutil.cpu_count()
            if count is not None:
                current["cpu_count"] = float(count)
            try:
                load = psutil.getloadavg()
                current.update(
                    zip(("cpu_load_1m", "cpu_load_5m", "cpu_load_15m"), load, strict=True)
                )
            except AttributeError, OSError, NotImplementedError:
                pass  # Unsupported metrics are absent, never substituted with zero.
            counters: dict[str, int] = {}
            try:
                io = psutil.disk_io_counters()
            except AttributeError, OSError, NotImplementedError, psutil.Error:
                io = None
            if io is not None:
                counters.update(
                    disk_read_bytes_per_second=io.read_bytes,
                    disk_write_bytes_per_second=io.write_bytes,
                )
            try:
                net = psutil.net_io_counters()
            except AttributeError, OSError, NotImplementedError, psutil.Error:
                net = None
            if net is not None:
                counters.update(
                    network_receive_bytes_per_second=net.bytes_recv,
                    network_transmit_bytes_per_second=net.bytes_sent,
                )
            now = time.monotonic()
            current.update(counter_rates(self._previous, now, counters))
            self._previous = (now, counters)
            with self._lock:
                self._snapshot = current
        except Exception:
            logger.error("Host sampling failed", exc_info=False)
            with self._lock:
                # Drop stale host samples: a scrape must not pretend collection is current.
                self._snapshot = {"sample_success": 0}
            self._previous = None

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return self._snapshot.copy()

    def collect(self) -> Iterator[GaugeMetricFamily]:
        snapshot = self.snapshot()
        labels = [self.settings.host_id, self.settings.host_name, self.settings.environment]
        for name, value in snapshot.items():
            metric = GaugeMetricFamily(
                "sentinel_host_" + name,
                METRICS[name],
                labels=["host_id", "host_name", "environment"],
            )
            metric.add_metric(labels, value)
            yield metric
