# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.event_loop import create_event_loop


def pytest_asyncio_loop_factories(config, item):
    return {"platform": create_event_loop}
