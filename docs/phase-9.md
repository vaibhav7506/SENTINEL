# Phase 9 — Hardening, tests and self-observability

Phase 9 is complete. **200 tests passed with zero skips:** 194 Python tests across seven suites, including ten real database integration tests, and six frontend tests. All four isolated database cohorts passed head → base → head migration round trips and schema parity checks. Phase 10 has not started.

The API now bounds request bodies, URI length, database statements and request duration; sanitizes validation/driver errors; logs generated IDs and route templates; and limits experiment starts and Console reads. The independent demo injector applies its own authentication, allowlists, body deadline/size and start-rate limit. Cancellation remains available. Injector timing must match the bounded request, and corrupt stored parameters cannot reach the injector.

Model loading rejects missing manifests, corruption, escaped artifact paths, reordered schemas, incompatible normalization/widths and non-finite parameters. Malformed feature records are skipped. Failed delivery attempts are counted, definite rejection retries stop at three, and ambiguous delivery remains unknown across restart. A disappeared host's expired forecast closes as unconfirmed, cancels pending delivery and does not claim service recovery.

All nine required self metrics are exported and scraped. Grafana's self dashboard has twelve panels, including committed/high-risk scores, queued alerts and unsuccessful attempts, inference/feature duration, LLM attempts/failures, worker errors and last successful work age. Counters describe work since process start; persisted totals remain database facts. Disabled LLM/alert activity honestly appears as zero.

The Console bounds response bytes, rejects malformed view contracts and uses a new controller for each polling attempt. Eight views passed keyboard/navigation and layout checks at desktop and mobile sizes, with no document overflow. During a real API outage, evaluation data remained visible as the last successful snapshot; normal polling reconnected automatically. During a real local Prometheus outage, health stayed 200, readiness became 503, and raw telemetry became unavailable with an empty series; restoration returned readiness and telemetry to normal. A closed-port database fixture returned safe 503 while liveness remained 200.

Both fresh live scores still reproduce the frozen model artifacts. The cutoff remains **0.9875243902206421**. Two independent offline training runs produced identical weights, preprocessing, history, scores, cutoff and evaluation without registration. Earlier Phase 7/8 evidence and model artifacts were hash-checked and preserved. All nine services are restored and healthy; inference and features continue running. No external LLM, Slack or RunbookOS transmission or remediation execution occurred.

The held-out model still missed all ten positive test windows, with recall/F1 **0** and poor calibration. The local hardening checks do not establish predictive success, production capacity or generalization. Read-only APIs retain the existing loopback development scope; production authentication, distributed limits, HTTPS and deployment remain gated Phase 10 work. Joblib artifacts remain trusted local files; hashes detect corruption rather than authenticate publishers.

Evidence:

- [Failure coverage and test plan](phase-9-test-plan.md)
- [Security review](phase-9-security-review.md)
- [Suite results](phase-9-tests.json) and [acceptance record](phase-9-validation.json)
- [Live metrics, Grafana and dependency checks](phase-9-runtime.json)
- [Fresh score reproduction and restart deduplication](phase-9-live-inference.json)
- [Prometheus outage/recovery](phase-9-prometheus-outage.json)
- [Browser checks](phase-9-browser.json), [mobile](phase-9-mobile.jpg), [disconnect](phase-9-disconnected.jpg), [restored Console](phase-9-overview.jpg)
- [Credential screening](phase-9-secret-screening.json): configured credentials absent from screened sources, evidence and local logs

Run `backend/.venv/Scripts/python.exe scripts/verify_phase9_tests.py` for all Python/database suites, `npm test` in `frontend`, and `backend/.venv/Scripts/python.exe scripts/verify_phase9_runtime.py` against the running local stack. Do not run older evidence-writing commands unless intentionally updating their original acceptance records.

**Stopped before Phase 10.**
