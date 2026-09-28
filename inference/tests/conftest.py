"""Use the same Psycopg-compatible Windows loop as the native worker."""

from app.core.event_loop import create_event_loop


def pytest_asyncio_loop_factories(config, item):
    return {"platform": create_event_loop}
