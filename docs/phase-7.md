# Phase 7 — governed RunbookOS proposals

Phase 7 is complete. **Stopped before Phase 8.** Sentinel can create and persist a diagnostics suggestion from a fresh high-scoring prediction and pass it into the RunbookOS adapter. This phase uses a clearly labeled mock intake. No real RunbookOS approval or remediation execution is claimed.

## Adapter scope

The local RunbookOS source was inspected without modification. Its control plane exposes organization-scoped incident webhooks, action authorization, human approval decisions, and workflow execution. Those APIs do not provide the requested standalone Sentinel proposal-intake contract using just a base URL and API key. Existing approval and execution routes require RunbookOS organization, incident, workflow/action and authenticated-user context. Sentinel does not call those execution routes or manufacture their identifiers.

The requested fallback is implemented in `backend/app/services/runbookos.py`: a typed `RunbookOSAdapter.submit(ProposalRequest)` interface and deterministic `MockRunbookOSAdapter`. The mock returns `mock://runbookos/proposals/<proposal-id>`, `approval_status=pending`, and `execution_status=not_started`. The stored reference is a mock receipt, not a real RunbookOS queue entry. The adapter has no approval or execution method. Actual human review and execution remain the responsibility of a future verified RunbookOS intake integration.

Only `collect_diagnostics` is suggested in this phase. `collect-diagnostics:v1` is Sentinel's symbolic suggestion reference, not a verified RunbookOS runbook ID. Restart, scale and service mutations are not selected or run. No shell, Kubernetes command or execution API is invoked by the proposal path.

## Configuration

```dotenv
RUNBOOKOS_ENABLED=false
RUNBOOKOS_BASE_URL=
RUNBOOKOS_API_KEY=
RUNBOOKOS_ADAPTER=mock
RUNBOOKOS_PROPOSAL_THRESHOLD=0.995
```

Integration defaults to disabled. In that mode the native inference worker performs no proposal database queries or adapter calls; normal scoring, explanations and alerting continue. Enabling it selects mock intake only. An unsupported adapter such as `http` is rejected during configuration validation. Base URL and secret API key settings are reserved for an agreed real intake contract; the mock makes no HTTP request and sends neither setting anywhere. The key is never returned by the status API.

The proposal threshold is configurable from zero to one. Eligibility also requires meeting the frozen model's alert threshold, using the latest host/model prediction, an unresolved matching incident, prediction and feature timestamps within the existing 90-second freshness limit, and an unexpired forecast anchored at feature-window end. No model, calibration or alert threshold was retuned. A numerical cutoff does not establish confidence: the held-out model still has recall/F1 zero and poor calibration.

## Durable proposal and approval boundary

Migration `0006_governed_proposals` adds prediction linkage, suggested action, factual reason, adapter kind, RunbookOS reference, intake status, acknowledgement time and safe error details to `RemediationProposal`. Incident, parameters, approval/execution state, and their timestamps are retained. A database unique constraint limits each incident/runbook suggestion to one proposal. A separate constraint forbids an execution state other than `not_started` unless approval is `approved`.

The worker commits a pending proposal before adapter intake, then durably marks it `sending`. Adapter calls have a five-second bound. Acknowledgement stores the mock reference and actual database time. Facts and identities are rechecked against the persisted prediction before submission. Resolved, superseded, stale or expired candidates are cancelled; altered facts and invalid contracts are rejected. Unsupported approval/execution claims or mismatched references are treated as invalid receipts. Transport errors and interrupted submissions become `unknown` with no automatic retry or successful-execution claim. Repeated cycles and worker restarts do not resubmit accepted proposals.

All mock receipts remain pending human approval with execution not started; `approved_at` and `executed_at` remain null. Sentinel exposes no approval or execute mutation. The read-only endpoints are:

- `GET /remediation/status`: enabled state, adapter scope, cutoff and human-approval requirement.
- `GET /remediation/proposals?limit=50`: stored proposals; limits are constrained to 1–100.

The native worker exposes separate `sentinel_proposal_submissions_total` and `sentinel_proposal_errors_total` metrics. Optional proposal-cycle errors preserve already stored inference scores. The console remains the foundation view with updated phase scope; full proposal and incident presentation belongs to Phase 8.

## Actual acceptance evidence

[The proposal demonstration](phase-7-proposal-demo.json) replays the real saved model's validation window at 2026-09-26 20:00 UTC in an isolated temporary PostgreSQL database. The frozen model reproduced probability **0.9875243902206421**. An explicitly test-only proposal cutoff of **0.98** allowed that historical score into the adapter; the live default remains **0.995**. Replay prediction timestamps and host/incident records are fixture context, not current forecasts or production incidents.

Exactly one proposal reached the recording mock adapter. It stored its proposal, incident and prediction IDs, diagnostics action, reason and `mock://` reference, with approval pending and execution not started. A second cycle created no additional submission. The temporary database was removed after verification. No fixture records were inserted into the live database, and no actual RunbookOS message or execution occurred.

[Live governance verification](phase-7-live-governance.json) confirms the API reports disabled integration, enforces list bounds, and uses the new migration. The live database contains zero proposals and zero remediation executions. [Live inference verification](phase-7-live-inference.json) reproduces a fresh 600-second prediction and separate anomaly score from the unchanged model, with zero inference/proposal cycle errors, a healthy Prometheus inference scrape and preserved local receiver deduplication.

## Checks and reproduction

**80 tests passed:** 68 backend tests, nine inference contracts, two isolated proposal tests and one isolated inference persistence test. Two additional optional backend database suites were not run; two proposal tests skipped in the general backend run were then executed against the isolated database. Proposal checks cover disabled mode, freshness/expiry and model cutoff, safe suggestions, idempotency, restart recovery, resolved and superseded incidents, altered facts, transport uncertainty, invalid execution claims, and database approval enforcement. Both proposal and inference migration round trips and Alembic schema parity passed.

Ruff lint and formatting, strict typing, frontend lint/type checking/build, container builds, and all nine-service stack checks pass. [The complete validation record](phase-7-validation.json) records counts and evidence hashes. Earlier model and Phase 6 acceptance artifacts are preserved.

From the repository root with the local stack running:

```powershell
uv run --project backend python -m pytest -c backend/pyproject.toml backend/tests -q
uv run --project inference python scripts/check_proposal_database.py
uv run --project inference python scripts/check_inference_database.py
uv run --project inference python scripts/verify_inference_state.py --output docs/phase-7-live-inference.json
python scripts/verify_stack.py
```

The isolated proposal command deliberately recreates the labeled historical demonstration and its evidence file. It does not enable integration in the live `.env`. To inspect actual stored proposals, open the API's `/docs` or the read-only proposal route. A real RunbookOS intake contract, authenticated status synchronization and human approval/execution demonstration remain unverified until that external integration exists.
