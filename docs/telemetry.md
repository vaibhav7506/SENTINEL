# Phase 2 telemetry

## Collection and metric contract

`sentinel-agent` samples psutil every five seconds and exposes the Prometheus text format on `/metrics`. Sampling is independent of scrapes; overlapping scrapes cannot corrupt CPU deltas or I/O rates. Snapshots are replaced atomically. Every host metric has only `host_id`, `host_name`, and `environment` labels. Device names, interface names, paths and PIDs are never labels.

All names begin `sentinel_host_`: CPU usage percent and count; CPU load 1m/5m/15m; memory usage percent, available bytes and capacity bytes; swap usage percent; disk usage percent and capacity bytes; aggregate disk read/write bytes per second; aggregate network receive/transmit bytes per second; process count; uptime seconds; sample timestamp seconds and sample success.

I/O rates use differences between actual psutil counters divided by monotonic elapsed seconds. Rates are absent on the first sample and after a reset, missing counter or collection failure. Unsupported load/I/O signals are absent, not replaced with invented zeroes. Zero swap usage on a machine with no swap is an actual psutil value. A failed core sample removes stale host values and publishes sample success 0. CPU deltas are primed before the first timed sample. Windows load averages have psutil's startup warm-up behavior and are not instantaneous CPU percentages.

## Local executable mode

```sh
uv sync --project agent --frozen
uv run --project agent sentinel-agent
```

Defaults are hostname identity, development environment, port 9101, loopback binding, five-second samples and filesystem root. Configure `HOST_ID`, `HOST_NAME`, `ENVIRONMENT`, `AGENT_PORT`, `AGENT_BIND_ADDRESS`, `AGENT_SAMPLE_SECONDS`, `AGENT_DISK_PATH`. On Windows, for example:

```powershell
$env:HOST_ID = 'windows-workstation'
$env:HOST_NAME = 'Windows workstation'
$env:AGENT_DISK_PATH = 'C:/'
uv run --project agent sentinel-agent
```

Prometheus's optional `sentinel-local-agent` job uses `host.docker.internal:9101` to reach the executable on Docker Desktop. This was verified working with a Windows loopback-bound agent. Other Docker hosts may need a gateway mapping and an appropriate bind address. If the executable is not running, that optional target correctly reports down. Keep metrics endpoints on trusted networks.

## Docker mode and scope

`docker compose up --build` includes the agent. It binds to its container network and is not published on a host port. Identity is `docker-vm`; `/metrics` is scraped at `sentinel-agent:9101`. Docker Desktop metrics do **not** represent the Windows host: CPU, memory, uptime and aggregate disk counters reflect the Linux VM, while process count and network counters reflect the container namespaces, and disk usage reflects the configured container filesystem/backing storage. The separate executable gives actual Windows host metrics. Do not compare these scopes as though they are the same machine.

## Kubernetes DaemonSet

`infra/kubernetes/sentinel-agent-daemonset.yaml` is an undeployed agent manifest. Replace its explicit image placeholder with an actual image you have published or loaded into your test cluster, and create the `sentinel` namespace before applying it. It uses node-name identity, host PID/network namespaces and read-only proc/root mounts to collect node metrics. The agent is non-root, drops all capabilities, has no service-account token, and needs no Kubernetes API permissions. Host access requires a namespace policy that permits this monitoring workload. Prometheus discovery/scraping must be configured in the target cluster; annotations alone do not create discovery. No Kubernetes deployment is claimed in Phase 2.

## Application and Sentinel metrics

Both FastAPI apps expose:

- `sentinel_http_requests_total{method,route,status_code}`
- `sentinel_http_errors_total{method,route,status_code}` for actual HTTP 4xx/5xx responses
- `sentinel_http_request_duration_seconds` histogram with method/route labels
- `sentinel_http_inflight_requests`

Labels use route templates; unmatched URLs collapse to `unmatched`, and unusual methods to `OTHER`. Request identifiers, user input and raw paths never become metric labels. `/metrics` is excluded from HTTP instrumentation. Unhandled errors count as 500, and in-flight counts unwind on exceptions. Error series appear after an actual error response; absence before the first error is not evidence of a missing outage.

The demo's `/work?units=500000` performs real bounded CPU computation in a thread. Inputs are validated and limited to 2,000,000 units. It does not inject latency, errors or failures. An invalid input produces a real HTTP 422 validation response. The placeholder worker now exposes `sentinel_worker_heartbeat_timestamp_seconds` on port 8002; it still generates no features or predictions.

## Dashboards and verification

Grafana provisions the `Sentinel` folder with Host Overview, Application Overview and Sentinel Self-Monitoring. All panels query the real Prometheus datasource. Host Overview includes a host dropdown. Self-monitoring shows scrape health, API throughput/latency, worker heartbeat age and agent sample age/success. No future inference metrics are scaffolded.

```sh
python scripts/load_demo.py --duration 60 --workers 4
python scripts/verify_stack.py
python scripts/verify_telemetry.py
```

Start the local executable before `verify_telemetry.py` to verify the Windows target too. Generate one actual HTTP validation error to populate the error metric, e.g. request `/work?units=2000001` (expected HTTP 422). Allow at least two Prometheus scrapes before checking rates. Verification queries actual Prometheus history and actual Grafana panel data frames; `docs/phase-2-validation.json` records the latest observed results. The load script is restricted to localhost addresses, bounded duration and bounded concurrency, and prints measured outcomes.
