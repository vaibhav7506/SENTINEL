from urllib.error import HTTPError

import pytest
from sentinel_agent import push
from sentinel_agent.identity import Identity


def test_batch_and_retry_preserve_identity_and_bound_buffer(monkeypatch):
    tick = [0.0]
    monkeypatch.setattr(push.time, "monotonic", lambda: tick[0])
    identity = Identity("http://127.0.0.1:8000", "fixture", "fixture-secret")
    sender = push.Sender(identity, 30)
    calls = []

    def failed(server, path, payload, credential):
        calls.append((path, payload, credential))
        raise HTTPError(server, 429, "Fixture", {"Retry-After": "60"}, None)

    monkeypatch.setattr(push, "post", failed)
    for _ in range(6):
        sender.add({"cpu_usage_percent": 42.0})
    assert sender.flush() is False
    tick[0] = 30
    with pytest.raises(HTTPError):
        sender.flush()
    assert len(sender.samples) == 6
    assert calls[0][0] == "/agent/v1/metrics"
    assert "account_id" not in calls[0][1] and "host_id" not in calls[0][1]
    tick[0] = 89
    assert sender.flush() is False
    monkeypatch.setattr(push, "post", lambda *args: {})
    tick[0] = 90
    assert sender.flush() and not sender.samples
    assert sender.identity is identity
    for _ in range(200):
        sender.add({"cpu_usage_percent": 1.0})
    assert len(sender.samples) == 120
