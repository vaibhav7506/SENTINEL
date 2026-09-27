"""Keep this Windows collection thread awake until a bounded UTC deadline."""

import argparse
import ctypes
import time
from datetime import UTC, datetime

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--until", required=True)
args = parser.parse_args()
end = datetime.fromisoformat(args.until)
if end.tzinfo is None or not 0 < (end - datetime.now(UTC)).total_seconds() <= 14400:
    raise ValueError("Deadline must be UTC-aware and within four hours")
try:
    if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
        raise RuntimeError("Windows rejected collection sleep prevention")
    while datetime.now(UTC) < end:
        time.sleep(min(20, (end - datetime.now(UTC)).total_seconds()))
finally:
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
