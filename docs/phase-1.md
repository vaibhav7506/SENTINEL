# Phase 1 — foundation acceptance record

Historical record at the Phase 1 boundary. See Phase 2 for the current implementation.

Verified locally on 2026-09-26. **Phase 1 complete.** No production or Kubernetes deployment is claimed. No work from Phase 2 has been started.

## Files created or changed

Created:

- `compose.yaml`: all seven required services, dependency ordering, health checks, persistent volumes and loopback-only host bindings.
- `.gitignore`, `.python-version`, root `uv.lock`, `Makefile`: local hygiene, Python baseline, legacy reproducibility and developer commands.
- `backend/pyproject.toml`, `backend/uv.lock`, `backend/Dockerfile`, `backend/.dockerignore`: Python 3.14 environment, locked dependencies and API/worker container.
- `backend/app/main.py`, package initializers, `api/health.py`, `core/config.py`, `core/logging.py`, `db/base.py`, `db/session.py`, `schemas/health.py`, `services/readiness.py`: modular API foundation, bounded readiness, validated configuration, JSON logging and engine lifecycle.
- `backend/app/workers/placeholder.py`: graceful placeholder process and real lifecycle/heartbeat logs.
- `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_timescaledb.py`: migration framework and TimescaleDB extension setup.
- `backend/tests/test_foundation.py`: 16 foundation test cases.
- `demo-service/app`, `demo-service/pyproject.toml`, `demo-service/uv.lock`, `demo-service/Dockerfile`, `demo-service/.dockerignore`: minimal real HTTP demo app.
- `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/eslint.config.js`, `frontend/index.html`, `frontend/src`, `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`: minimal React/TypeScript/Vite/Tailwind console, live API status, development and container proxies.
- `prometheus/prometheus.yml`, `grafana/provisioning/datasources/prometheus.yml`: Prometheus self-scrape and real Grafana datasource.
- `scripts/init_env.py`, `scripts/verify_stack.py`, `README.md`, `docs/dependencies.md`, this record: local credential initialization, read-only smoke checks, development instructions, architecture, version verification and scope documentation.

Changed:

- `.env.example`: Phase 1 configuration, blank credentials and configurable local ports.
- Root `pyproject.toml`: preserved poller package declares its actual imports; unused ML/scheduler dependencies are deferred. Original `sentinel`, `config` and SQLite data remain outside Compose.
- `sentinel/poller.py`: classify connector errors using the underlying OS message so aiohttp's generic “Cannot connect” prefix does not misclassify DNS failures.
- `tests/test_poller.py`: connector-error fixtures now include valid connection context instead of passing `None`, which broke exception formatting.

An ignored `.env` was generated with random local credentials; it is never printed or committed. Original data files are preserved.

## Tests and checks executed

| Validation | Actual result |
| --- | --- |
| Foundation pytest suite | 16 passed, no warnings |
| Original poller/config/storage suite | 18 passed |
| Ruff checks for backend, demo app and scripts | Passed |
| Ruff formatting checks | 23 files already formatted |
| Strict mypy for backend | Passed, 16 source files |
| Strict mypy for demo app | Passed, 2 source files |
| Frontend ESLint | Passed |
| Frontend TypeScript | Passed |
| Vite production build | Passed |
| Three uv lock consistency checks (offline) | Passed |
| Docker Compose configuration validation | Passed |
| API, worker, frontend and demo container builds | Passed |
| Docker Compose startup and wait | Seven running services; six configured probes healthy; worker process running |
| `scripts/verify_stack.py` | All seven HTTP checks and Compose service checks passed |
| Real Alembic migration | `0001_timescaledb (head)` applied; extension version 2.30.1 |
| Real migration downgrade/upgrade round trip | Passed; extension deliberately preserved on downgrade |
| Grafana provisioned datasource health | OK; successfully queried Prometheus API |
| Real Prometheus self-scrape | Stored `up{job="prometheus"}=1` |
| Real dependency failures | Prometheus and database individually stopped: HTTP 503 and matching dependency down; HTTP 200 liveness |
| Real dependency recovery | Both dependencies restored and readiness returned HTTP 200 |
| Browser rendering | Container-built console displayed Foundation ready and both dependencies UP |
| Browser unavailable-data state | Development preview displayed API unreachable without fabricated data |
| Git ignore checks | `.env`, tools, runtimes, dependency directories and caches ignored |

Initial checks uncovered and resolved SQLAlchemy's missing asyncio extra, an incompatible TypeScript/ESLint parser combination, React effect lint, missing Vite CSS declarations, a deprecated test-client interface, two legacy fixture errors and the DNS classification bug. Runtime verification also resolved a frontend health probe that selected IPv6 localhost while Nginx listened on IPv4. The final probe uses 127.0.0.1.

Windows already had PostgreSQL listening on 5432 and reserved port 3000. Sentinel uses host database port 15432 and Grafana port 4300; container ports remain unchanged. Grafana plugin preinstallation is disabled to avoid background downloads changing the local foundation unexpectedly.

## Acceptance criteria

The initialized repository starts through `docker compose up` (or `docker compose up --build` for initial image builds). All required components were verified working locally:

- Sentinel API: http://localhost:8000/docs
- TimescaleDB: localhost:15432
- Prometheus: http://localhost:9090
- Grafana: http://localhost:4300
- Sentinel Console: http://localhost:5173
- Demo service: http://localhost:8001
- Placeholder worker: running and emitting actual JSON heartbeat logs.

`GET /ready` visibly reports both database and Prometheus health, and was tested against actual dependency outages. `GET /health` independently reports API liveness. The frontend uses the real readiness response through its same-origin API proxy.

Reproduce the checks with the commands in README and `python scripts/verify_stack.py`. The local stack remains running for inspection.

## Limitations and unresolved issues

No unresolved Phase 1 failure remains. This is a local development foundation. The worker is intentionally a placeholder. Host and application telemetry, Grafana dashboards, domain tables, feature generation, ML training, evaluation, incidents, LLM summaries, alerting, remediation, full Console pages, CI/CD and Kubernetes are not implemented in this phase. No prediction, incident, model score or deployment result has been fabricated.

Docker Desktop with Linux containers must be running. A private `.env` must be generated before Compose starts. Compose publishes only local loopback ports and does not provide public TLS or production authentication; production hardening and deployment belong to later phases.

**Stopped after Phase 1.** Continue only on explicit authorization: `Continue to Phase 2`.
