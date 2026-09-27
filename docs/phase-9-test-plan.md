# Phase 9 test plan and failure coverage

Critical paths are telemetry → causal features → frozen model → durable prediction/incident → bounded delivery. Fixtures exercise transport failures without sending to external LLM, Slack or RunbookOS services. Predictive performance continues to use the original held-out evidence.

| Required case | Verification |
|---|---|
| Missing Prometheus data | `backend/tests/test_features.py`: empty discovery produces no host/window or successful-work timestamp |
| Host disappears | Stale/disappearing-host discovery; Console unknown state; expired unconfirmed incident closes and queued delivery cancels without claiming recovery |
| Partial metrics | Optional query failures retain missing values, record query errors and preserve available CPU/memory samples |
| Bad model file | Actual corrupted checkpoint bytes rejected before deserialization; required manifest and digest checks |
| Feature schema mismatch | Reordered schema rejected against frozen dataset; reordered input and mismatched feature map skipped |
| Database unavailable | Safe API exception tests; real closed local PostgreSQL port returns health 200, ready/snapshot 503; no driver details |
| Prometheus unavailable | Connection/HTTP failure tests; actual local dependency outage and recovery check |
| LLM unavailable | Gateway connection failure, oversized response and unsupported IDs all select deterministic supplied-fact fallback; attempts/failures counted |
| Slack unavailable | Mock Slack HTTP 503/transport tests; durable channel-independent delivery retries stop after three rejections |
| Duplicate alert | Isolated unique outbox record, stable idempotency key, interrupted sending becomes unknown without retry |
| Duplicate incident | Repeated high windows and worker restart retain one condition; risk clearing permits a new condition |
| Corrupted experiment record | Invalid persisted cancellation parameters return safe 409 before any injector call |
| RunbookOS unavailable | Isolated adapter failure produces unknown intake outcome, preserves scoring and never executes remediation |
| Worker restart | Isolated durable dedup and interrupted delivery/proposal recovery; verified native process restart and live fresh scoring |
| API restart | Browser retains the successful snapshot on disconnect and reconnects after service restoration |

| Model contract | Verification |
|---|---|
| Training reproducibility | Two complete CPU fits of frozen real evidence produce exactly equal tensors, preprocessing, history, scores, cutoff and evaluation; registration disabled |
| Input ordering | Names, digest, version and ordered feature map must match; incompatible vectors rejected |
| No future-data leakage | Future samples excluded; imputation/scaling/background use training only; calibration/cutoff use validation; input/label intervals and experiment groups cannot overlap partitions |
| Threshold behavior | Validation threshold selection; below-cutoff scores skip SHAP; real historical crossing produces additive SHAP; committed high-risk counters tested |
| Artifact compatibility | Frozen score reproduction; report/schema/normalizer dimensions and fit partitions; finite weights/scales/calibration; MLP/Isolation Forest widths |

API tests also cover authentication before validation, disabled/production chaos gates, fixed target/namespace, bounded injector expiry, URI/body/deadline limits, cancellation access during rate limiting, CORS defaults, safe errors and low-cardinality metrics. Integration tests use four separately created databases and perform head → base → head migrations and schema parity checks in each; all are dropped in `finally`.

Frontend tests use Node's test runner, the existing TypeScript compiler and React server rendering. They verify missing versus zero values, escaped hostile text, chart gaps/cutoff, keyboard-accessible tables, missing explanations, invalid/oversized API responses and request abort/recovery. Browser checks exercise all eight views, filters, partitions, desktop/mobile layout and real API disconnect/recovery.

Run `backend/.venv/Scripts/python.exe scripts/verify_phase9_tests.py`, then `npm test` in `frontend`. Production code typing and linting, container builds, runtime checks and browser evidence are recorded in the phase acceptance report. This is a local hardening campaign; it does not establish production capacity or predictive generalization.
