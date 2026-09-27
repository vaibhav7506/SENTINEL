"""Verify a real selected Kubernetes release and trusted HTTPS; manifests alone never pass."""

import argparse
import base64
import getpass
import json
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward Console credentials to HTTP or a different origin."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        source = urllib.parse.urlparse(request.full_url)
        target = urllib.parse.urlparse(newurl)
        if (
            target.scheme != "https"
            or (target.hostname, target.port or 443) != (source.hostname, source.port or 443)
            or target.username
            or target.password
        ):
            raise ValueError("Refusing credential redirect outside the trusted HTTPS origin")
        return super().redirect_request(request, fp, code, message, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def verify_http_entrypoint(https_url):
    """Only a refused connection or anonymous redirect to the selected HTTPS origin passes."""
    selected = urllib.parse.urlparse(https_url)
    host = selected.hostname
    if ":" in host:
        host = "[" + host + "]"
    http_url = selected._replace(scheme="http", netloc=host).geturl()
    try:
        with urllib.request.build_opener(NoRedirect()).open(http_url, timeout=15):
            raise ValueError("Public HTTP serves content; require HTTPS-only routing")
    except urllib.error.HTTPError as error:
        if error.code not in {301, 302, 307, 308} or error.headers.get("WWW-Authenticate"):
            raise ValueError("Public HTTP must not serve a login challenge or content") from error
        destination = urllib.parse.urlparse(
            urllib.parse.urljoin(http_url, error.headers.get("Location", ""))
        )
        if (
            destination.scheme != "https"
            or (destination.hostname, destination.port or 443)
            != (selected.hostname, selected.port or 443)
            or destination.username
            or destination.password
        ):
            raise ValueError(
                "Public HTTP redirect does not reach the trusted HTTPS origin"
            ) from error
        return "redirects_to_selected_https_origin"
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return "connection_refused"
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", default="sentinel")
    parser.add_argument("--url", required=True)
    parser.add_argument("--grafana-url")
    parser.add_argument("--username", default="sentinel")
    parser.add_argument("--without-demo", action="store_true")
    parser.add_argument("--without-node-agent", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("Provide an HTTPS URL without embedded credentials")
    if args.output.exists():
        raise SystemExit("Choose a new verification evidence file")
    password = getpass.getpass("Console password: ")
    auth = "Basic " + base64.b64encode((args.username + ":" + password).encode()).decode()
    trusted_opener = urllib.request.build_opener(TrustedRedirect())
    kubectl = ["kubectl", "--context", args.context, "--namespace", args.namespace]

    def command(*parts):
        return subprocess.run(
            [*kubectl, *parts], capture_output=True, text=True, check=True, timeout=60
        ).stdout

    def fetch(url, authorization=None):
        headers = {"Authorization": authorization} if authorization else {}
        # Default SSL verification is required. No insecure skip option is provided.
        with trusted_opener.open(
            urllib.request.Request(url, headers=headers), timeout=15
        ) as response:
            return response.status, response.read()

    version = json.loads(command("version", "-o", "json"))["serverVersion"]
    minor = int("".join(c for c in version["minor"] if c.isdigit()))
    if version["major"] != "1" or minor not in {35, 36, 37}:
        raise RuntimeError(
            "Cluster is outside the supported Kubernetes target verified for this release"
        )
    pods = json.loads(
        command("get", "pods", "-l", "app.kubernetes.io/instance=" + args.release, "-o", "json")
    )["items"]
    expected = {"api", "worker", "inference", "frontend", "prometheus", "grafana"}
    if not args.without_demo:
        expected |= {"demo", "observer"}
    if not args.without_node_agent:
        expected.add("node-agent")
    present = {pod["metadata"]["labels"].get("component") for pod in pods}
    if not expected.issubset(present):
        raise RuntimeError("Required deployed components are absent")
    for pod in pods:
        if not any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in pod["status"].get("conditions", [])
        ):
            raise RuntimeError("A deployed pod is not Ready")
    http_entrypoint = verify_http_entrypoint(args.url)
    try:
        fetch(args.url)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
    else:
        raise RuntimeError("Public Console is anonymously accessible")
    assert fetch(args.url, auth)[0] == 200
    ready = json.loads(fetch(args.url.rstrip("/") + "/api/ready", auth)[1])
    assert ready["status"] == "ready"
    snapshot = json.loads(fetch(args.url.rstrip("/") + "/api/console/snapshot", auth)[1])
    assert snapshot["hosts"] and snapshot["models"]
    inference = json.loads(
        command(
            "exec",
            "deployment/" + args.release + "-inference",
            "--",
            "python",
            "-m",
            "inference.verify",
        )
    )
    assert inference["status"] == "passed" and inference["frozen_scores_reproduced"]
    # Read metrics through a temporary loopback port-forward; Prometheus remains private.
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    process = subprocess.Popen(
        [
            *kubectl,
            "port-forward",
            "--address",
            "127.0.0.1",
            "service/" + args.release + "-prometheus",
            f"{port}:9090",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                assert fetch(f"http://127.0.0.1:{port}/-/ready")[0] == 200
                break
            except OSError, urllib.error.URLError:
                time.sleep(1)
        else:
            raise RuntimeError("Prometheus readiness deadline exceeded")

        def query(expression):
            payload = json.loads(
                fetch(
                    f"http://127.0.0.1:{port}/api/v1/query?query="
                    + urllib.parse.quote(expression, safe="")
                )[1]
            )
            assert payload["status"] == "success"
            return payload["data"]["result"]

        jobs = ["sentinel-api", "sentinel-worker", "sentinel-inference"]
        if not args.without_demo:
            jobs += ["demo-service", "sentinel-observer", "sentinel-demo-agent"]
        if not args.without_node_agent:
            jobs.append("sentinel-agent")
        for job in jobs:
            results = query('up{job="' + job + '"}')
            assert results and all(float(row["value"][1]) == 1 for row in results), job
        loaded = query("sentinel_model_loaded")
        assert loaded and all(float(row["value"][1]) == 1 for row in loaded)
        for worker in ("features", "inference"):
            results = query(
                'time() - sentinel_worker_last_success_timestamp{worker="' + worker + '"}'
            )
            assert results and all(0 <= float(row["value"][1]) <= 180 for row in results), worker
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if args.grafana_url:
        if urllib.parse.urlparse(args.grafana_url).scheme != "https":
            raise ValueError("Grafana URL must use HTTPS")
        assert fetch(args.grafana_url.rstrip("/") + "/api/health")[0] == 200
    report = {
        "status": "passed",
        "verified_at": datetime.now(UTC).isoformat(),
        "context": args.context,
        "namespace": args.namespace,
        "release": args.release,
        "https_url": args.url,
        "trusted_https_and_console_auth": True,
        "http_entrypoint": http_entrypoint,
        "ready_pods": len(pods),
        "scraped_jobs": jobs,
        "frozen_inference": inference,
        "safe_demo_experiment": (
            "Run scripts/run_demo.py through the private operator API port-forward; "
            "attach its evidence before declaring Phase 10 complete"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print("PASS real deployment, authenticated HTTPS, agent scrapes and fresh frozen scores")


if __name__ == "__main__":
    main()
