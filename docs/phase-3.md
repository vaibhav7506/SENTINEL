# Phase 3 — feature pipeline acceptance record

**Historical Phase 3 record: verified locally on 2026-09-26.** Current progress is recorded in Phase 4. No model has been trained and no failure labels have been generated.

## Files created or changed

Created `backend/app/features/{__init__,schema,engineering,prometheus}.py`, `backend/app/workers/features.py`, `backend/app/models.py`, `backend/app/core/event_loop.py`, `backend/alembic/versions/0002_features.py`, `backend/tests/{conftest,test_features,test_feature_database}.py`, `scripts/{check_feature_database,verify_features}.py`, `docs/features.md`, this acceptance record, and the actual `docs/phase-3-validation.json` results.

Updated backend settings, Alembic model registration, readiness revision checks, health phase, foundation tests, Compose worker command/configuration, `.env.example`, Makefile, README, dependency notes, and the historical Phase 2 record. The Console's phase/scope text now reflects completed telemetry and features; the full Console remains Phase 8 work. Removed the superseded placeholder worker. Existing verification/load scripts received formatting only. No package dependencies or locks changed. The original poller and stored data remain preserved.

## Tests and checks

- Backend: **34 tests passed**, covering known statistics, irregular timestamps, causal boundaries, future-sample leakage, deterministic ordering/hash, missing signals, malformed/failed Prometheus responses, stale hosts, ambiguous exporters, worker host-failure isolation, and existing health/HTTP contracts. One opt-in database test is skipped in this ordinary run.
- Isolated database integration: **1 test passed**, verifying an actual TimescaleDB hypertable, immutable retries, and atomic host/window rollback. Migration upgrade, downgrade to the extension revision, re-upgrade, and Alembic schema parity checks all passed. Temporary test databases were removed, including one left by an interrupted local connection diagnostic.
- Agent: **12 tests passed**; demo: **1 test passed**; original prototype: **18 tests passed**. **66 tests passed across ordinary and opt-in runs.**
- Backend/helper Ruff lint and formatting passed; strict backend mypy passed for 23 source files. Frontend lint, TypeScript checking, and production builds passed.
- Final Compose image build and startup passed. All eight services run and all seven configured readiness/health probes pass. The feature worker is running and producing windows. Final stack verification passed API liveness/readiness, frontend and proxy, Prometheus, Grafana, demo, and container checks.
- Native Windows PostgreSQL checks use IPv4 loopback and a supported selector event loop; Docker uses the normal Linux loop. The discovered TimescaleDB time index is represented in model metadata, avoiding false migration differences.

## Real acceptance evidence

A bounded 150-second run with four local demo clients completed **1,632 successful requests and 0 failed load requests**. These are HTTP outcomes, not model evaluation results.

The read-only 75-second observation recorded growth from **24 to 28 feature windows**. Both `docker-vm` and `windows-workstation` had consecutive minute windows and finite CPU/memory trends. Stored vectors had **209 values** in schema order, version `v1-w900-e60-r300`, matching the schema hash. Every observed sample timestamp lay inside its window and at or before its end. No optional-query errors were recorded in the verified rows.

The Docker host had real demo-service request-rate and histogram-derived latency features. Windows had no assigned application job; its application features were explicitly null while host features were persisted successfully. Raw samples remain in Prometheus. The hypertable was verified through TimescaleDB metadata. All eight future-phase tables were empty.

After the final image restart, SQL inspection showed **19 windows for each host**, with latest window end **2026-09-26 15:13:00 UTC**. This is a historical acceptance snapshot; the running worker continues to add windows. The original observed values and timestamps are in [phase-3-validation.json](phase-3-validation.json).

## Limitations and remaining phases

These are candidate features, not validated predictors. Short/sparse observation history is recorded rather than imputed; inference eligibility and preprocessing are deferred. Error-count rate is errors per second, and disk throughput interactions are not disk utilization or saturation measurements. Explicit job mapping and agent telemetry scope matter; the Docker agent does not measure the Windows host.

There is no labeled dataset, training, evaluation, online prediction, alert delivery, chaos execution, or remediation. Future persistence tables define contracts only. The minimal Console shows dependency readiness, not feature tables or predictions. Fleet-scale worker coordination, feature retention, and downtime backfill are unimplemented. See [features.md](features.md) for exact statistics, causality, quality metadata, configuration, and verification commands.
