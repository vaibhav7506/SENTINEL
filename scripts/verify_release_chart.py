"""Render real Helm releases and reject unsafe configurations without a cluster."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infra/helm/sentinel"


class UniqueLoader(yaml.SafeLoader):
    """Duplicate YAML keys can silently replace security controls."""


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError("Duplicate YAML key: " + str(key))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    common = [args.helm, "template", "sentinel", str(CHART), "--namespace", "sentinel-demo"]
    demo = ["--set", "imageRegistry=sentinel-local", "--set", "imageTag=phase10"]
    digests = [
        item
        for name in ("api", "worker", "agent", "frontend", "demo", "inference")
        for item in ("--set", "imageDigests." + name + "=sha256:" + "1" * 64)
    ]
    production = (
        demo
        + digests
        + [
            "--set",
            "environment=production",
            "--set",
            "database.internal=false",
            "--set",
            "database.host=db.example.invalid",
            "--set",
            "database.externalCIDR=192.0.2.0/24",
            "--set",
            "ingress.enabled=true",
            "--set",
            "ingress.className=traefik",
            "--set",
            "ingress.host=sentinel.example.invalid",
            "--set",
            "ingress.tlsSecret=existing-tls",
        ]
    )
    reports = []
    for name, values in (
        ("demo", demo),
        ("production", production),
        (
            "minimal",
            production + ["--set", "demo.enabled=false", "--set", "nodeAgent.enabled=false"],
        ),
    ):
        subprocess.run(
            [args.helm, "lint", str(CHART), *values], check=True, capture_output=True, text=True
        )
        process = subprocess.run([*common, *values], check=True, capture_output=True, text=True)
        documents = [d for d in yaml.load_all(process.stdout, Loader=UniqueLoader) if d]
        if args.render_dir:
            args.render_dir.mkdir(parents=True, exist_ok=True)
            (args.render_dir / (name + ".yaml")).write_text(process.stdout, encoding="utf-8")
        assert not any(d["kind"] == "Secret" for d in documents)
        for doc in documents:
            if doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}:
                pod = doc["spec"]["template"]["spec"]
                assert pod["securityContext"]["runAsNonRoot"] is True
                for container in pod["containers"]:
                    assert container["securityContext"]["allowPrivilegeEscalation"] is False
                    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
                    assert container["resources"]["requests"] and container["resources"]["limits"]
                    assert "livenessProbe" in container and "readinessProbe" in container
                for container in pod.get("initContainers", []):
                    assert container["securityContext"]["allowPrivilegeEscalation"] is False
                    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
                    assert container["resources"]["requests"] and container["resources"]["limits"]
                    if container["name"] == "wait-for-api":
                        assert not container.get("env") and not container.get("envFrom")
                if doc["metadata"]["name"] == "sentinel-frontend":
                    auth_volume = next(v for v in pod["volumes"] if v["name"] == "frontend-auth")
                    assert {v["key"] for v in auth_volume["secret"]["items"]} == {
                        "htpasswd",
                        "api-proxy.conf",
                    }
                if pod.get("automountServiceAccountToken"):
                    assert doc["metadata"]["name"] == "sentinel-prometheus"
        role = next(d for d in documents if d["kind"] == "Role")
        grafana = next(
            d
            for d in documents
            if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "sentinel-grafana"
        )
        assert "uid: sentinel-prometheus" in grafana["data"]["datasource.yaml"]
        assert role["rules"] == [
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
        ]
        if name != "demo":
            assert not any(d["kind"] == "StatefulSet" for d in documents)
            ingress = next(d for d in documents if d["kind"] == "Ingress")
            assert ingress["spec"]["tls"][0]["secretName"] == "existing-tls"
        config = next(
            d
            for d in documents
            if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "sentinel-frontend"
        )
        assert "auth_basic_user_file" in config["data"]["default.conf"]
        assert "location ^~ /api/chaos/ { return 403; }" in config["data"]["default.conf"]
        reports.append(
            {
                "fixture": name,
                "resource_count": len(documents),
                "status": "passed",
                "rendered_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
            }
        )
    unsafe = [
        [],
        demo + ["--set", "environment=production"],
        production + ["--set", "chaos.enabled=true"],
        production + ["--set", "imageDigests.inference="],
        production + ["--set", "ingress.tlsSecret="],
        production + ["--set", "database.externalCIDR="],
    ]
    for values in unsafe:
        assert subprocess.run([*common, *values], capture_output=True).returncode != 0
    for path in (ROOT / "grafana/dashboards").glob("*.json"):
        assert path.read_bytes() == (CHART / "dashboards" / path.name).read_bytes()
    print(
        json.dumps(
            {
                "status": "passed",
                "renders": reports,
                "unsafe_configurations_rejected": len(unsafe),
                "deployed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
