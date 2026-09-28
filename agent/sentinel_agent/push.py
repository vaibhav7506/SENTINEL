"""Bounded retry buffer; one request carries multiple real sample snapshots."""

import time
from datetime import UTC, datetime
from urllib.error import HTTPError

from sentinel_agent.identity import Identity, post


class Sender:
    def __init__(self, identity: Identity, interval: int) -> None:
        self.identity, self.interval = identity, interval
        self.samples: list[dict[str, object]] = []
        self.next_send = time.monotonic() + interval
        self.backoff = interval

    def add(self, values: dict[str, float]) -> None:
        self.samples.append({"timestamp": datetime.now(UTC).isoformat(), "values": values})
        self.samples = self.samples[-120:]

    def flush(self) -> bool:
        now = time.monotonic()
        if now < self.next_send or not self.samples:
            return False
        cutoff = datetime.now(UTC).timestamp() - 1740
        self.samples = [
            s
            for s in self.samples
            if datetime.fromisoformat(str(s["timestamp"])).timestamp() >= cutoff
        ]
        if not self.samples:
            self.next_send = now + self.interval
            return False
        try:
            post(
                self.identity.server,
                "/agent/v1/metrics",
                {
                    "schema_version": 1,
                    "samples": self.samples,
                    "reporting_interval_seconds": self.interval,
                    "agent_version": "0.3.0",
                },
                self.identity.credential,
            )
        except HTTPError as error:
            self.backoff = min(300, max(self.backoff * 2, self.interval))
            if error.code == 429:
                try:
                    self.backoff = min(300, max(self.backoff, int(error.headers["Retry-After"])))
                except ValueError, TypeError, KeyError:
                    pass
            self.next_send = now + self.backoff
            raise
        except Exception:
            self.backoff = min(300, self.backoff * 2)
            self.next_send = now + self.backoff
            raise
        self.samples.clear()
        self.backoff = self.interval
        self.next_send = now + self.interval
        return True
