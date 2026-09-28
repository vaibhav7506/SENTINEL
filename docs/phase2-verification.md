# Phase 2 local verification

Verified on 2026-09-27 against the existing Sentinel baseline. Phase 2 acceptance passed locally. The running console at http://127.0.0.1:5173 now uses account authentication. This record does not claim hosted deployment, remote CI execution or improved model quality.

## Acceptance evidence

| Requirement | Observed result |
| --- | --- |
| Registration, login, logout and two accounts | Actual PostgreSQL-backed sessions passed; browser switching showed each account's own hosts and cleared the previous view |
| OWNER/ADMIN/MEMBER | Server-side read/write tests passed; MEMBER browser navigation and host actions were restricted; the last owner cannot be removed |
| One-time enrollment | Replay, expiry, revocation and concurrent consumption passed; one race winner received a host identity |
| Host-specific permanent key | Independent keys, hash-only storage, individual revocation and unknown/revoked-key rejection passed |
| Server-derived ownership | Requests containing agent account_id or host_id were rejected; downstream ownership guards rejected mismatched references |
| Protected identity and restart | Windows ACL and Linux permission tests passed; actual psutil enrollment/upload reused the same identity after restart |
| Existing demo migration | Original database upgraded to 0007_saas_foundation; every original column value and row count matched before/after |
| Application scoping | Account A could not read or mutate B's hosts, metrics, features, predictions, incidents, alerts, experiments, credentials, channels, proposals or team |
| PostgreSQL RLS | Actual non-owner sentinel_app transactions denied cross-account reads/updates; missing context returned no tenant rows; pool reuse reapplied context |
| Security audit | Required security actions produced audit entries; UPDATE and DELETE were rejected, including under the owner connection |
| Inference compatibility | All 46 frozen inference/training tests passed on Windows and again on Linux; existing database worker cohorts passed |
| CI coverage | The checked-in workflow includes five disposable database cohorts, tenant adversarial tests, migration round trips and agent bundle freshness |

The RLS tests use PostgreSQL 17 and TimescaleDB 2.30.1, including both raw metrics and feature-window hypertables. The tested role is not a table owner, superuser or BYPASSRLS role. Trusted bootstrap, authentication and existing worker connections retain owner privileges; browser transactions explicitly switch to sentinel_app. Hosted provisioning must support creation and granting of that role.

## Test totals and other checks

| Cohort | Unique tests passed |
| --- | ---: |
| Backend unit contracts | 122 |
| Inference and training contracts | 46 |
| Agent contracts across Windows/Linux | 20 |
| Demo service | 11 |
| Preserved prototype | 18 |
| PostgreSQL persistence and tenant adversarial contracts | 19 |
| Frontend session contracts | 7 |
| Total | 243 |

Windows agent execution passed 19 tests and skipped the Unix permission contract. Linux passed all eight identity tests, covering that contract. The Linux inference run repeated 46 tests; those repetitions are not added to the unique total. Database tests ran in five isolated cohorts of 2, 5, 2, 2 and 8 tests. Each completed head/base/head migrations and Alembic schema comparison without drift.

Ruff passed with 135 files already formatted. Strict mypy passed for 51 backend/inference/training source files and five agent source files. Frontend lint, type checking and production build passed. The deterministic agent bundle passed its source/checksum check; its Linux installer passed bash syntax validation. Helm rendered 104 resources across demo, production and minimal fixtures, and rejected six unsafe configurations. These are render checks, not evidence of a deployed cluster.

The original Compose stack passed API health/readiness, frontend proxy, Prometheus, Grafana and demo probes: nine services running, seven health checks healthy. Anonymous console access requires a session. Browser testing exercised account switching, logout, token issuance/revocation and MEMBER restrictions using disposable test accounts. A real local psutil agent persisted 19 metric values; restart created no duplicate host. No installer was run against the user's operating system.

## Original demo preservation

The API, feature worker and observer were paused while a private database backup was taken. Migration and comparison ran in one transaction. Only after all original counts and original-column hashes matched was that transaction committed and the services resumed. Live telemetry can add rows afterward; the table below records the migration boundary.

| Table | Before | After |
| --- | ---: | ---: |
| hosts | 2 | 2 |
| feature_windows | 895 | 895 |
| predictions | 473 | 473 |
| incidents | 30 | 30 |
| alerts | 60 | 60 |
| chaos_experiments | 11 | 11 |
| failure_events | 11 | 11 |
| service_observations | 20,981 | 20,981 |
| remediation_proposals | 0 | 0 |
| model_versions | 1 | 1 |
| evaluation_runs | 1 | 1 |

All old tenant rows belong to the clearly named Sentinel development/demo account. The 78 protected model, dataset, historical evidence and ML lockfile hashes remain unchanged. Bootstrap was separately tested against a restored database: its owner could log in and see that restored demo's records, and a second bootstrap attempt was refused.

The actual local demo account has no default user or password. Create its first owner with a private password at the terminal prompts:

```sh
docker compose exec sentinel-api python -m app.saas.bootstrap --email YOUR_EMAIL
```

Signup creates a separate account. It does not claim the preserved demo records.

## Evidence and publication boundary

Local evidence is kept in ignored `.runtime/phase2`, including `original-migration.json`, `migration-preservation.json`, `bootstrap.json`, `live-agent.json`, `linux-database.log`, `linux-inference.log`, `linux-identity.log`, browser screenshots, chart renders and `final.json`. Backups, fixture credentials and service variables remain private and are not public artifacts.

The frozen baseline publication completed 32 commits at [vaibhav7506/SENTINEL](https://github.com/vaibhav7506/SENTINEL), ending at `4af3190d6fa7e306d6044ff7ee95148ccded8e6e`. The remote main ref matched that commit, the local chain matched all recorded batches, and the shortest recorded commit interval was 301.539 seconds. The publication heartbeat is paused. Phase 2 source changes are local and outside the frozen baseline publication manifest.

The existing model missed every positive held-out window and is not validated for useful operational forecasting. Passing compatibility tests preserves behavior; it does not establish predictive accuracy. New enrolled host telemetry is stored and displayed, but no distributed scoring pipeline was added. Railway deployment, external telemetry, hosted role provisioning, remote CI and real Slack delivery remain unverified. Phase 3 has not started.
