# Phase 8 — Sentinel Console

Completed the eight requested React/TypeScript views: Overview, Hosts, Host Detail, Predictions, Incidents, Experiments, Models, and Evaluation. Open the running [local console](http://127.0.0.1:5173). Phase 9 has not started.

The console uses a compact observability layout with a persistent desktop navigation, mobile menu, restrained signal colors, labeled tables, and timestamped charts. Search and health filters, keyboard navigation, accessible controls, focus management, reduced-motion support, loading and empty states, an error boundary, explicit disconnection notices, retained snapshots, and retry controls are implemented. Data refreshes every 30 seconds while the page is visible.

## Data and interpretation

`GET /console/snapshot` reads actual persisted hosts, scores, incidents, deliveries, proposals, experiments, model versions, failure events, and EvaluationRun records. Counts use the database rather than the bounded visible ledger. Each record ledger exposes its total and a latest-100 limit; host inventory is bounded to 1,000 displayed hosts. Health and high-risk counts cover the full registered inventory. Search filters the loaded records. No fixture data populates the live console.

Observed health comes from independent service observations no older than 90 seconds or an unrecovered failure event. An unobserved or stale service is **unknown**. Fresh below-threshold model probability does not establish healthy service behavior. High risk requires a fresh score crossing that score's registered model threshold.

`GET /console/hosts/{host_id}` returns that host's score history, enriched incidents and four real Prometheus metric series over the last 30 minutes: CPU, memory, disk utilization and network receive throughput. Queries escape host labels, use fixed metric selectors and bounded timeouts, reject nonfinite chart values, and expose stale or unavailable telemetry explicitly. Host pages link to the provisioned `sentinel-hosts` Grafana dashboard with the corresponding host variable. Model charts break across gaps rather than implying continuous recorded observations. No separate confidence estimate exists; the score history is labeled as experimental probability with poor calibration.

Incident evidence separates predicted, observed, confirmed and resolved timestamps. A related prediction is fetched by identity even after it leaves the 100-record prediction ledger. SHAP drivers, source-labeled LLM selections or deterministic summaries, delivered/pending alert status and mock RunbookOS intake/approval/execution states come from stored records. Summary text remains interpretation; observer timestamps establish recorded degradation. The console offers no remediation approval or execution control. Intake remains disabled in the live environment.

Experiments distinguish no observed failure, no eligible pre-failure scores, no advance warning, a recorded warning and a delivered alert. Useful lead requires at least 60 seconds before observed onset and a forecast horizon covering that onset. Score warning and actual delivery lead are separate fields. Late delivery receives no advance lead. Experiments before online scoring began show missing coverage rather than a fabricated model miss.

Evaluation metrics come directly from `EvaluationRun.metrics`. Test and validation partitions have separate controls, classification outcomes, calibration bars, failure-type results, warning coverage and provenance. Average lead excludes unwarned events and displays missing when no useful warning exists. False-alert rate includes its small eligible negative exposure. The held-out test still has **0 precision, recall and F1**, ten missed positive windows, PR-AUC **0.668** against random ranking **0.667**, and only five non-failure minutes. No operational predictive success is claimed.

## Verification

**88 tests passed:** 87 backend tests and one additional isolated PostgreSQL console test. Five optional database tests skip in the general run; the console test is then executed separately. Four other optional database tests were not rerun for this phase. The new contracts cover fresh/stale observed health, future timestamps, forecast coverage, useful lead and late delivery, missing hosts, finite telemetry, failed Prometheus requests, escaped labels and omitted transport payloads. The isolated SQL test verifies prediction enrichment beyond the ledger limit, warning coverage across all eligible records, and alert/incident/prediction joins. Its disposable fixture database is removed and live records are preserved.

Ruff lint and formatting pass for 87 Python files. Strict typing passes for 36 production source files. Frontend lint, TypeScript checking, production build and both updated container builds pass. The nine-service stack and seven HTTP/health checks pass. The live inference verifier reproduces both separate scores from the unchanged frozen artifacts.

[Live console evidence](phase-8-live-console.json) compares actual prediction values, model metadata, evaluation metrics and split definitions with PostgreSQL, checks the two host detail routes and validates that all console routes are read-only GET operations. The Docker host has healthy current service observations and four raw telemetry series; the stale Windows host is unknown. The two Phase 6 live experiments still show no advance warning, while older experiments have no online scoring coverage. There are zero live incidents, alerts and proposals; their empty states are real.

[Browser evidence](phase-8-browser.json) records all eight views at 1440px desktop width and the seven main views at 390px mobile width with no document overflow. Host detail was also checked at mobile width. Host search, unknown-health filtering, test/validation switching, raw telemetry, menu focus, Escape and navigation focus were exercised. A brief local API interruption showed the disconnected notice with the last successful snapshot; restoring the API recovered live data. Loading was observed. Earlier Phase 6 and Phase 7 evidence remains preserved.

Screenshots: [overview](phase-8-overview.jpg), [host detail](phase-8-host.jpg), [held-out evaluation](phase-8-evaluation.jpg), [mobile overview](phase-8-mobile.jpg), and [disconnected state](phase-8-disconnected.jpg).

## Reproduce

From the repository root with the local stack running:

```powershell
uv run --project backend python -m pytest -c backend/pyproject.toml backend/tests -q -p no:cacheprovider
uv run --project backend python scripts/check_console_database.py
uv run --project backend python scripts/verify_console.py
uv run --project inference python scripts/verify_inference_state.py --output docs/phase-8-live-inference.json
python scripts/verify_stack.py
```

The complete [validation record](phase-8-validation.json) stores checks and evidence hashes. The environment remains local development. Broader failure testing, production security hardening and deployment are later phases. This phase leaves the model threshold, scoring artifacts, chaos controls and governed execution boundary unchanged.
