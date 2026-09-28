import logging
import signal
from threading import Event

from prometheus_client import CollectorRegistry, start_http_server

from sentinel_agent.collector import HostCollector
from sentinel_agent.config import Settings
from sentinel_agent.identity import load
from sentinel_agent.push import Sender


def main() -> None:
    settings = Settings()
    identity = load(settings.agent_identity_path) if settings.agent_identity_path else None
    if identity:
        settings = settings.model_copy(update={"host_id": identity.host_id})
    sender = Sender(identity, settings.agent_reporting_seconds) if identity else None
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    collector = HostCollector(settings)
    registry = CollectorRegistry()
    registry.register(collector)
    server, thread = start_http_server(
        settings.agent_port, addr=settings.agent_bind_address, registry=registry
    )
    try:
        while not stop.wait(settings.agent_sample_seconds):
            collector.sample()
            if sender:
                snapshot = collector.snapshot()
                if snapshot.get("sample_success") == 1:
                    try:
                        sender.add(snapshot)
                        sender.flush()
                    except Exception:
                        logging.warning("Agent telemetry delivery failed; identity was retained")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
