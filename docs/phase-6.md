# Phase 6 — live inference, explanations and local alerts

Implemented with live campaign evidence recorded on 2026-09-26 UTC. **Phase 6 is complete. Stopped before Phase 7.** Final source, database and restored live-state verification passed on 2026-09-26 at 22:14 UTC (2026-09-27 at 03:44 IST).

Fresh real feature windows now produce persisted calibrated MLP probabilities and separate Isolation Forest unfamiliarity scores. Two live controlled faults completed and recovered, but neither crossed the frozen model threshold. There were **no live pre-failure alerts and no measured alert lead times**. This phase demonstrates the inference and alert infrastructure, not successful predictive detection.

## Actual live acceptance

The unchanged Phase 5 model is `20260926T204848-mlp-1bcd8acd`; threshold is **0.9875243902206421**, horizon 600 seconds. No retraining, threshold adjustment or synthetic live predictions were used.

| Actual experiment | First sustained breach (UTC) | Confirmed | Recovered | Pre-onset probabilities | Pre-failure alerts / lead |
| --- | --- | --- | --- | --- | --- |
| Latency `417e4016-9aa4-4420-9be6-af62b587c784` | 21:26:42.417617 | 21:26:48.021218 | 21:27:26.840508 | 0.140481–0.141326 | 0 / null |
| HTTP 503 `94c83246-2cd5-42ca-a87f-c7a73f2cc2d9` | 21:27:49.427518 | 21:27:53.505501 | 21:28:34.308830 | 0.140481–0.152425 | 0 / null |

All timestamps are 2026-09-26. The campaign used the existing allowlisted demo service and objective three-consecutive-breach criteria. It checked eight actual online predictions, unique feature windows, separate anomaly scores and zero remediation records. See [live validation](phase-6-live-validation.json) and [campaign observations](phase-6-live-campaign.json).

The held-out evaluation remains unchanged: recall/F1 zero, PR-AUC 0.668 versus baseline 0.667, with poor calibration. The model is not validated for operational prediction. The acceptance condition “when the trained model successfully detects it” was not met in these faults; no detection success is claimed.

## Inference and persistence

The continuous feature worker queries Prometheus every minute and persists causal 900-second windows. A separate native inference worker polls every five seconds, scores only matching schema and fresh windows (at most 90 seconds old), and rejects future or invalid input. Frozen artifact hashes and registered metadata must match. Training-only transformations are reused without online fitting.

Each prediction records the actual database prediction time, feature window end, probability, anomaly score, explanation and horizon. Forecast validity ends at **feature window end + 600 seconds**, not inference time + 600. A database advisory lease permits one worker, and a unique host/model/window constraint prevents duplicate scores.

One unresolved incident per host and one alert per incident/channel prevent repeated warnings for the same condition, including restarts. A 300-second channel cooldown delays new deliveries. Three consecutive below-threshold windows within 180 seconds clear unresolved risk; objectively observed recovery also resolves incidents. An expired unconfirmed forecast is labeled explicitly.

Alert delivery is durable before HTTP. Successful acknowledgements store the actual database acknowledgement time. HTTP 429/5xx responses retry at most three attempts with backoff; permanent 4xx responses fail. Transport uncertainty or interruption leaves delivery `unknown` without a blind automatic retry. External delivery cannot promise exactly-once semantics; generic receivers should honor the stable `Idempotency-Key`.

Confirmed failures are correlated with the actual forecast horizon and incident predictions. An acknowledgement after degradation or outside the horizon never receives a positive pre-failure lead. Pending alerts are cancelled when their incident resolves.

## SHAP and factual summaries

SHAP runs only for high scores opening a condition or crossing upward again, including renewed crossings inside an unresolved incident. Normal scores do not run SHAP. Bounded permutation SHAP uses the 16 training rows, 209 features, seed 1729 and 1,676 evaluations. Contributions use calibrated probability units, with additivity checked and the ten largest absolute associations retained. Correlated inputs and approximation limit interpretation; these are associations, not causal root causes. Explanation failure preserves the real score with an explicit unavailable status.

`LLM_PROVIDER=disabled` is the current configuration. Deterministic structured summaries include supplied evidence, checks, possible pattern and confidence notes. No LLM key or external provider was used.

An optional `LLM_PROVIDER=http` adapter posts to exactly `LLM_BASE_URL`, with `LLM_MODEL` and optional bearer `LLM_API_KEY`. This is a **custom gateway contract**, not a direct vendor API: request JSON contains model, instruction, numbered facts and allowed checks; response JSON must contain only `evidence_ids` (integers) and `checks` (allowed keys). Prose, extra fields, unsupported IDs, timeouts, oversized responses and provider failures fall back deterministically. Displayed text is rendered from supplied facts; the provider cannot invent operational claims or execute remediation.

## Local delivery verification

The user selected a local receiver. Both configured destinations point to loopback port 8091 (`/generic` and `/slack`); no external Slack messages were sent.

A real historical validation window at 20:00 UTC was replayed through the frozen model, SHAP and summary/delivery adapters. Its probability was 0.9875243902206421 and SHAP additivity residual was zero. The receiver accepted both formats and suppressed duplicate IDs. Actual acknowledgements occurred around 21:26:59, after that historical failure at 20:05:02; timing is therefore `after_degradation`, lead null. These labeled contract receipts are not live predictions or predictive successes and were not inserted as live incidents. See [explanation and delivery evidence](phase-6-explanation-delivery.json) and [receiver receipts](phase-6-webhook-receipts.jsonl).

## Running locally

The local platform uses nine Docker services, plus the native inference worker and local receiver. Docker Engine 29.6.2 and all services are restored. The stale socket directories were preserved and recreated together; container volumes and credentials were preserved. See [the recovery record](docker-recovery.md). Both native processes have restarted. The worker rejected incomplete post-outage windows until real CPU coverage exceeded the unchanged 720-second minimum, then persisted a fresh score at 22:14 UTC. The CPU PyTorch dependency is deliberately separate from the API image. Linux/Kubernetes ML deployment has not been implemented.

From the repository root, with Docker running:

```powershell
uv sync --project inference --frozen
# In one terminal:
uv run --project inference python scripts/run_local_webhook_receiver.py
# In another terminal:
uv run --project inference python -m inference.worker
```

Set `ALERT_GENERIC_WEBHOOK_URL=http://127.0.0.1:8091/generic` and `ALERT_SLACK_WEBHOOK_URL=http://127.0.0.1:8091/slack` in the ignored root `.env`. Keep `LLM_PROVIDER=disabled` for this local setup. The receiver persists verification receipts; changing destinations requires restarting the worker. Do not launch a second worker while the existing one is running. Metrics bind to loopback port 8004; Prometheus reaches that port through Docker Desktop's host gateway. The receiver binds to loopback 8091.

Read-only API routes expose actual data: `/inference/status`, `/predictions`, `/incidents`, `/alerts`. Lists accept `limit` from 1 to 100. Status reports recent, stale or absent persisted scores and actual model metadata. This remains the minimal console; the complete incident interface belongs to Phase 8.

## Validation

Real frozen-score reproduction, SHAP additivity, causal/freshness rejection, honest alert timing, factual fallback and webhook response contracts passed. An isolated database test verifies persistent deduplication, repeated crossings, restart behavior, risk clearing and interrupted delivery. Migration upgrade/downgrade/upgrade and ORM parity passed without changing live data. The current rerun passed nine inference contract tests, 60 backend tests (two optional database tests skipped), lint and formatting across 75 files, strict typing across 32 source files including the live verifier, and frontend lint, type checking and build. Nine interrupted files were formatted without changing the frozen training artifacts. After Docker recovery, the isolated database persistence test and migration round trip passed, as did the nine-service stack and all seven HTTP checks. The 65-package inference lockfile passed offline validation. Both local receiver formats retained receipt deduplication across restart. Fresh inference verification now passes: the 22:14 UTC prediction has a 600-second horizon, its probability and separate anomaly score reproduce from the unchanged frozen model, and its validity ends at 22:24 UTC. Database checks found 18 predictions, zero duplicate prediction groups, zero alerts, no active experiments, no unrecovered events, and no remediation proposals at verification. Worker cycle errors are zero, the Prometheus inference scrape is up, all three list APIs enforce their limits, and both receiver formats suppress the existing receipt IDs after restart. See [restored live evidence](phase-6-restored-live.json). The prior campaign and historical explanation/receipt artifacts retain their original hashes. [Validation](phase-6-validation.json) records each check and the remaining work explicitly.

```powershell
uv run --project inference python -m pytest -c inference/pyproject.toml inference/tests/test_contracts.py -q
uv run --project inference python scripts/check_inference_database.py
python scripts/verify_stack.py
uv run --project inference python scripts/verify_inference_state.py
```

`scripts/verify_live_inference.py` deliberately runs real bounded demo faults; `scripts/verify_explanation_delivery.py` sends labeled historical checks only to the local receiver. Phase 5's verifier contains historical phase-boundary assertions that later tables were empty; use the Phase 6 checks after online inference starts. Phase 5 artifacts and its training lockfile remain unchanged.

The restored-state verifier compares an actual fresh stored prediction with the frozen artifacts, checks causal coverage and forecast expiry, API list bounds, prediction uniqueness, worker metrics and Prometheus scraping, and replays existing local receipt IDs to verify restart deduplication without adding receipts. Exit code 2 means eligible fresh history is still accumulating.
