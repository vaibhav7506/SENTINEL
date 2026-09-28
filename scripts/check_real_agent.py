"""Real psutil agent lifecycle against loopback only; never print its permanent key."""

import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".runtime/phase3"


def main() -> None:
    server = os.environ.get("SENTINEL_AGENT_SMOKE_API_URL", "http://127.0.0.1:8000")
    parsed = urlsplit(server)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path:
        raise SystemExit("Agent smoke checks require a loopback HTTP API")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    identity = OUTPUT / ("agent-smoke-" + secrets.token_hex(6)) / "agent.toml"
    python = ROOT / "agent/.venv/Scripts/python.exe"
    if os.name != "nt":
        python = ROOT / "agent/.venv/bin/python"
    environment = {
        **os.environ,
        "AGENT_IDENTITY_PATH": str(identity),
        "AGENT_PORT": "19231",
        "AGENT_SAMPLE_SECONDS": "1",
        "AGENT_REPORTING_SECONDS": "5",
        "AGENT_DISK_PATH": str(ROOT.anchor),
        "ENVIRONMENT": "test",
    }
    created = []
    with httpx.Client(base_url=server, timeout=10) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": secrets.token_hex(12) + "@local-agent.test",
                "password": secrets.token_urlsafe(24),
                "account_name": "Real Windows agent local verification",
            },
        )
        response.raise_for_status()
        csrf = {"X-CSRF-Token": client.cookies["sentinel_csrf"]}
        issued = client.post("/hosts/enrollment-tokens", json={}, headers=csrf)
        issued.raise_for_status()
        source = (
            "import json,sys; from pathlib import Path; "
            "from sentinel_agent.identity import enroll; p=json.load(sys.stdin); "
            "enroll(p['server'],p['token'],Path(p['path'])); "
            "print('protected identity enrolled')"
        )
        command = [str(python), "-c", source]
        private = json.dumps(
            {
                "server": server,
                "token": issued.json()["token"],
                "path": str(identity),
            }
        )
        subprocess.run(
            command,
            input=private,
            text=True,
            env=environment,
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        digest = hashlib.sha256(identity.read_bytes()).hexdigest()
        try:
            with (identity.parent / "agent.log").open("w", encoding="utf-8") as log:
                for cycle in range(2):
                    # Enrollment with the consumed token must reuse the protected identity.
                    subprocess.run(
                        command,
                        input=private,
                        text=True,
                        env=environment,
                        cwd=ROOT,
                        check=True,
                        capture_output=True,
                    )
                    process = subprocess.Popen(
                        [str(python), "-m", "sentinel_agent.main"],
                        cwd=ROOT,
                        env=environment,
                        stdout=log,
                        stderr=log,
                    )
                    created.append(process)
                    time.sleep(12)
                    hosts = client.get("/hosts").json()
                    assert len(hosts) == 1 and hosts[0]["status"] == "Online"
                    host = hosts[0]["id"]
                    samples = client.get(f"/hosts/{host}/metrics").json()
                    assert len(samples) >= (5 if cycle == 0 else 10)
                    assert samples[-1]["values"]["memory_capacity_bytes"] > 0
                    assert hashlib.sha256(identity.read_bytes()).hexdigest() == digest
                    process.terminate()
                    process.wait(timeout=10)
            credentials = client.get(f"/hosts/{host}/credentials").json()
            assert len(credentials) == 1
            revoked = client.post(
                "/agent-credentials/" + credentials[0]["id"] + "/revoke", headers=csrf
            )
            revoked.raise_for_status()
            source = (
                "import sys; from pathlib import Path; from urllib.error import HTTPError; "
                "from sentinel_agent.identity import load,post; from datetime import datetime,UTC; "
                "key=load(Path(sys.argv[1]));\ntry:\n "
                "post(key.server,'/agent/v1/metrics',{'schema_version':1,'samples':["
                "{'timestamp':datetime.now(UTC).isoformat(),'values':{'cpu_usage_percent':1.0}}"
                "]},key.credential)\nexcept HTTPError as e:\n print(e.code)"
            )
            denied = subprocess.run(
                [str(python), "-c", source, str(identity)],
                env=environment,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            assert denied.stdout.strip() == "401"
            time.sleep(17)
            assert client.get("/hosts").json()[0]["status"] == "Stale"
            time.sleep(35)
            assert client.get("/hosts").json()[0]["status"] == "Offline"
            evidence = {
                "scope": "Real Windows psutil agent; local loopback only",
                "host_id": host,
                "real_samples": len(samples),
                "enrollment": True,
                "protected_identity_sha256": digest,
                "restart_same_host_and_credential": True,
                "revoked_push_status": 401,
                "online_stale_offline_verified": True,
                "production_verified": False,
            }
            (OUTPUT / "agent-smoke.json").write_text(
                json.dumps(evidence, indent=2), encoding="utf-8"
            )
            print(
                "PASS real sampling, protected identity, restart, "
                "heartbeat transitions and revocation"
            )
        finally:
            for process in created:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
