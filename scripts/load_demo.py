"""Generate bounded real demo traffic and print measured HTTP outcomes only."""

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

parser = argparse.ArgumentParser()
parser.add_argument("--duration", type=int, default=60, choices=range(1, 601), metavar="1..600")
parser.add_argument("--workers", type=int, default=4, choices=range(1, 17), metavar="1..16")
parser.add_argument("--url", default="http://127.0.0.1:8001/work?units=500000")
args = parser.parse_args()
if not args.url.startswith(("http://127.0.0.1:", "http://localhost:")):
    raise SystemExit("Load generation is restricted to local demo addresses.")
end = time.monotonic() + args.duration


def run():
    successes = failures = 0
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(args.url, timeout=15) as response:
                response.read()
                successes += int(response.status == 200)
                failures += int(response.status != 200)
        except Exception:
            failures += 1
    return successes, failures


with ThreadPoolExecutor(max_workers=args.workers) as executor:
    outcomes = list(executor.map(lambda _: run(), range(args.workers)))
print(
    json.dumps(
        {
            "duration_seconds": args.duration,
            "workers": args.workers,
            "successful_requests": sum(x[0] for x in outcomes),
            "failed_requests": sum(x[1] for x in outcomes),
        }
    )
)
