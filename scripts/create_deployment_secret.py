"""Create private deployment credentials locally; never print plaintext secrets."""

import argparse
import base64
import getpass
import json
import secrets
from pathlib import Path

import bcrypt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="sentinel-demo")
    parser.add_argument("--name", default="sentinel-credentials")
    parser.add_argument("--output", type=Path, default=Path(".runtime/deployment-secret.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to((root / ".runtime").resolve()) or output.exists():
        raise SystemExit(
            "Use a new private output under .runtime; existing credentials are never overwritten"
        )
    password = getpass.getpass(
        "Console password (at least 16 characters, at most 72 UTF-8 bytes): "
    )
    if len(password) < 16 or len(password.encode()) > 72:
        raise SystemExit("Password does not meet length requirements")
    token = secrets.token_urlsafe(48)
    values = {
        "api-read-token": token,
        "api-proxy.conf": 'proxy_set_header Authorization "Bearer ' + token + '";\n',
        "htpasswd": "sentinel:"
        + bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        + "\n",
        "postgres-password": secrets.token_urlsafe(48),
        "grafana-password": secrets.token_urlsafe(48),
        "chaos-token": secrets.token_urlsafe(48),
    }
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": args.name, "namespace": args.namespace},
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing Secret during concurrent invocations.
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, indent=2) + "\n")
    output.chmod(0o600)
    print("Private Secret created under .runtime; do not commit or upload this file.")


if __name__ == "__main__":
    main()
