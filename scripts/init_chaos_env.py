"""Explicitly enable only bounded local demo chaos and generate a private token."""

import argparse
import secrets
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--enable-local-demo", action="store_true", required=True)
parser.parse_args()
root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = path.read_text(encoding="utf-8")
values = dict(
    line.split("=", 1) for line in content.splitlines() if "=" in line and not line.startswith("#")
)
environment = values.get("ENVIRONMENT", "development")
if environment not in {"development", "test", "demo"}:
    raise SystemExit("Refusing to enable chaos outside development/test/demo")
updates = {
    "CHAOS_ENABLED": "true",
    "CHAOS_ALLOWED_ENVIRONMENTS": '["' + environment + '"]',
    "CHAOS_ALLOWED_NAMESPACES": '["sentinel-demo"]',
    "CHAOS_ALLOWED_TARGETS": '["demo-service"]',
    "CHAOS_CONTROL_TOKEN": values.get("CHAOS_CONTROL_TOKEN") or secrets.token_urlsafe(32),
}
lines = [line for line in content.splitlines() if line.split("=", 1)[0] not in updates]
path.write_text(
    "\n".join(lines) + "\n" + "\n".join(key + "=" + value for key, value in updates.items()) + "\n",
    encoding="utf-8",
)
print("Enabled bounded local demo chaos in private .env; no token was printed")
