# Phase 2 — telemetry acceptance record

**Phase 2 complete, verified locally on 2026-09-26.** This is a historical record; current progress is documented in Phase 3.

## Files created or changed

Created the `agent` Python project, uv lock, Dockerfile and executable entry point with settings, sampler/collector and 12 tests; `infra/kubernetes/sentinel-agent-daemonset.yaml`; backend/demo HTTP instrumentation modules and tests; three `grafana/dashboards` JSON dashboards and dashboard provisioning; `scripts/load_demo.py`, `scripts/verify_telemetry.py`, `docs/telemetry.md`, and the actual `docs/phase-2-validation.json` artifact.

Updated backend/demo dependency declarations and locks, API/demo startup instrumentation, demo `/work`, placeholder worker heartbeat exporter, `compose.yaml`, `prometheus/prometheus.yml`, `.env.example`, Makefile, stack verifier, README and dependency documentation. The Phase 1 acceptance record is marked historical. The existing poller, configuration and stored data are preserved.

## Tests and checks

- Agent: **12 tests passed** for real sampling, rate deltas, counter resets, first-sample omission, bounded labels, missing/unsupported signals, failure handling and invalid settings.
- Backend: **18 tests passed**, including HTTP metric contracts, bounded unmatched route labels, unhandled errors and in-flight cleanup.
- Demo service: **1 test passed**, covering real bounded computation, validation errors and metric export.
- Original poller/config/storage: **18 regression tests passed**.
- **49 Python tests passed overall.**
- Ruff lint and formatting passed for agent, backend, demo and helper scripts.
- Strict mypy passed for agent (4 files), backend (17 files) and demo (3 files).
- Compose configuration and API/worker/demo/agent container builds passed. Eight Compose services run; seven configured probes are healthy. The placeholder worker runs and its exporter is scraped.
- All required Prometheus targets, plus the separately running Windows executable, are scraped successfully.
- Real host CPU and memory values changed in recorded Prometheus history. Throughput, process count and uptime samples are present.
- Real application request counts, latency histogram, HTTP error count and in-flight metrics are present. A real HTTP 422 validation response was used to check error instrumentation; it is not a chaos failure or label.
- A 60-second load run with four clients completed **748 successful requests and 0 failed load requests**. These are observed HTTP outcomes, not model evaluation results.
- Grafana's actual datasource queries returned numeric data for **12/12 Host Overview**, **4/4 Application Overview**, and **6/6 Sentinel Self-Monitoring** panels. No sample chart values are hardcoded.
- `scripts/verify_stack.py` and `scripts/verify_telemetry.py` passed. The telemetry verifier stores real observed checks in the JSON artifact.

## Local use

```sh
docker compose up --build
uv sync --project agent --frozen
uv run --project agent sentinel-agent
python scripts/load_demo.py --duration 60 --workers 4
python scripts/verify_telemetry.py
```

On Windows, set `AGENT_DISK_PATH=C:/` and stable host identity as shown in `docs/telemetry.md`. The local executable listens at http://localhost:9101/metrics; Prometheus reaches it through Docker Desktop's host gateway. The Compose agent is private to the container network. Grafana is at http://localhost:4300, using the generated local credentials from `.env`; dashboards are in the Sentinel folder.

The verified Docker stack and Windows executable remain running for inspection. Prometheus is the only raw telemetry store; no raw samples are copied into PostgreSQL.

## Limitations

The Docker agent's Linux VM and container namespace scope differs from the actual Windows host. This is documented and distinguished with separate identities. The optional local-agent target reports down when the executable is stopped; other Docker environments may require gateway/bind configuration. Windows load average startup values follow psutil's warm-up behavior. Initial/reset I/O rates and unsupported signals are omitted.

The DaemonSet manifest has an explicit image placeholder and was not deployed to a Kubernetes cluster. It needs a real published/loaded image, a namespace permitting host monitoring, and configured Prometheus discovery. No Kubernetes or production deployment is claimed.

The worker is still a placeholder. Feature engineering, domain tables, models, predictions, chaos experiments and alerting are unimplemented. The custom Console remains its minimal foundation view; Grafana supplies the real telemetry views. No failure prediction or predictive performance is claimed.

**Stopped after Phase 2.** Continue only when authorized to proceed to Phase 3.
