"""Psycopg-compatible event loops, including native Windows workers."""

import asyncio
import selectors
import sys


def create_event_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()
