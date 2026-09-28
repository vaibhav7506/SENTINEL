# ruff: noqa: E402
import os
import sys
from pathlib import Path

# Existing suites exercise the explicit single-user compatibility mode.
os.environ.setdefault("SAAS_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.event_loop import create_event_loop


def pytest_asyncio_loop_factories(config, item):
    return {"platform": create_event_loop}
