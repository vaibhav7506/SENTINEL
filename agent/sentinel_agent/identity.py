"""One-time enrollment and protected, reusable host identity; no account is client-selected."""

import argparse
import getpass
import json
import os
import platform
import socket
import ssl
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def server_url(value: str) -> str:
    parsed = urlparse(value)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise ValueError("Use HTTPS, or loopback HTTP for local development")
    return value.rstrip("/")


@dataclass(frozen=True)
class Identity:
    server: str
    host_id: str
    credential: str


def default_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Sentinel" / "agent.toml"
    return Path("/etc/sentinel/agent.toml")


def protect(path: Path) -> None:
    if os.name == "nt":
        username = os.environ.get("USERDOMAIN", "") + "\\" + getpass.getuser()
        process = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", username + ":F", "*S-1-5-18:F"],
            capture_output=True,
        )
        if process.returncode:
            raise ValueError("Could not restrict identity file permissions")
    else:
        path.chmod(0o600)


def save(identity: Identity, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise ValueError("Identity directory must not be a symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        protect(path)
        payload = (
            "\n".join(
                k + " = " + json.dumps(v)
                for k, v in {
                    "server": identity.server,
                    "host_id": identity.host_id,
                    "credential": identity.credential,
                }.items()
            )
            + "\n"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load(path: Path) -> Identity:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError("Invalid identity file")
        if os.name != "nt" and (
            stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != getattr(os, "geteuid", lambda: -1)()
        ):
            raise ValueError("Identity must belong to this user with mode 0600")
        value = tomllib.load(source)
    identity = Identity(
        server_url(value["server"]), str(value["host_id"]), str(value["credential"])
    )
    if (
        not identity.credential.startswith("sk_host_")
        or not 32 <= len(identity.credential) <= 128
        or not 1 <= len(identity.host_id) <= 128
    ):
        raise ValueError("Invalid identity fields")
    return identity


def post(
    server: str, path: str, payload: dict[str, object], credential: str | None = None
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    request = Request(
        server_url(server) + path,
        data=json.dumps(payload, allow_nan=False).encode(),
        headers=headers,
        method="POST",
    )
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=5) as response:
        body = response.read(16385)
        if len(body) > 16384:
            raise ValueError("Agent response too large")
        return json.loads(body) if body else {}


def enroll(server: str, token: str, path: Path) -> Identity:
    if path.exists():
        return load(path)
    response = post(
        server,
        "/agent/enroll",
        {
            "enrollment_token": token,
            "hostname": socket.gethostname(),
            "agent_version": "0.3.0",
            "platform": platform.system(),
            "architecture": platform.machine(),
        },
    )
    identity = Identity(server_url(server), str(response["host_id"]), str(response["credential"]))
    save(identity, path)
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--enrollment-token", required=True)
    parser.add_argument("--identity-path", type=Path, default=default_path())
    args = parser.parse_args()
    identity = enroll(args.server, args.enrollment_token, args.identity_path)
    print("Host enrolled; protected identity saved for " + identity.host_id)


if __name__ == "__main__":
    main()
