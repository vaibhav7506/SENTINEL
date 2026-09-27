import logging
import signal
from threading import Event

from prometheus_client import CollectorRegistry, start_http_server

from sentinel_agent.collector import HostCollector
from sentinel_agent.config import Settings


def main() -> None:
    settings = Settings()
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
