"""Create private Oracle settings without printing credentials or replacing an existing file."""

import argparse
import base64
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".env.oracle")
    args = parser.parse_args()
    hostname = args.hostname.lower()
    if (
        len(hostname) > 253
        or "." not in hostname
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
        or re.fullmatch(r"[0-9.]+", hostname)
    ):
        raise SystemExit("Provide a DNS hostname without a scheme, port, path or credentials")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or any(
        part in {".git", ".codex", ".agents"} for part in output.relative_to(ROOT).parts
    ):
        raise SystemExit("Choose a private settings file inside the Sentinel directory")
    content = (ROOT / "infra/oracle/env.example").read_text(encoding="utf-8")
    content = content.replace("SENTINEL_DOMAIN=sentinel.example.com", f"SENTINEL_DOMAIN={hostname}")
    for key in ("REDIS_PASSWORD",):
        content = content.replace(f"{key}=\n", f"{key}={secrets.token_urlsafe(32)}\n")
    content = content.replace(
        "INTEGRATION_ENCRYPTION_KEY=\n",
        "INTEGRATION_ENCRYPTION_KEY="
        + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        + "\n",
    )
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SystemExit("Oracle settings already exist; left unchanged") from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    print("Created private Oracle settings. Passwords were not displayed.")


if __name__ == "__main__":
    main()
