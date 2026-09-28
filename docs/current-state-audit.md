# Current-state audit

Executed on 2026-09-27 against source, fresh local containers and disposable database fixtures. Status describes the tested implementation, not production readiness or predictive accuracy. The frozen model has **zero held-out recall and F1**. Optional external integrations and deployment are not accepted by this audit.

| Subsystem | Status | Executed evidence and limits |
| --- | --- | --- |
| FastAPI | WORKING | Real `/health`, `/ready`, console and operational endpoints; 119 backend unit tests and database API checks. |
| Telemetry agent | WORKING | 12 tests; real psutil sampling and Prometheus scrapes. CPU, memory, disk I/O, network and visible process count changed under bounded local load. Docker process/network namespaces limit visibility. |
| Application metric collection | WORKING | Actual `/work` requests, ten actual 404 responses, increasing request/error counters, latency histograms and objective service probes. |
| Prometheus | WORKING | Real range/instant queries, persisted metric history and causal replay. Stopping the isolated service made API readiness return 503. |
| PostgreSQL/TimescaleDB | WORKING | Fresh database migration to `0006_governed_proposals`; feature hypertable checked; 11 integration tests across disposable databases, upgrade/downgrade/upgrade and schema drift checks. |
| Feature engineering | WORKING | 209 features replayed from real Prometheus data with zero differences. Statistics, missing data, causal time boundaries, cross signals, ordering and schema covered by executable tests. |
| Feature schema/versioning | WORKING | `v1-w900-e60-r300`, SHA-256 `ac1a64a8b97f9fed8b7f35c55dccb2013adb8dbd33cc2881ccdcb4a311da8227`; persisted ordering matches the frozen model. |
| PyTorch MLP pipeline | WORKING | Training/save/load tests and actual online probabilities reproduced from persisted inputs. This is execution verification: held-out predictive performance failed. |
| Unsupervised detector | WORKING | Existing healthy-training Isolation Forest loaded and produced a separate, reproducible anomaly score. It is not substituted for failure probability. |
| Model artifacts | WORKING | Hash-validated frozen weights, preprocessing, calibration, schema and dataset; 78 protected model/dataset/historical-evidence/lock files unchanged. |
| Training pipeline | WORKING | 17 training tests, including real training/reproducibility/save-load; rerun on Linux CPU. No replacement of the frozen production artifact. |
| Evaluation pipeline | WORKING | Recomputed scores and evaluation from actual held-out rows; train/validation/test experiment identities and input/label intervals checked for leakage. Results below remain poor. |
| Useful predictive performance | UNVERIFIED | Fresh evaluation: precision/recall/F1 0, ten false negatives. Live fault produced no new pre-failure warning. No operational prediction claim. |
| Chaos/failure injection | WORKING | One actual 45-second allowlisted development latency fault, independent breach/confirmation/recovery observations, cancellation and real post-recovery score. No forced probability. |
| SHAP | WORKING | Actual high-risk frozen-data and live-model permutation SHAP; real feature names, train background and additivity checks. Attribution is association, not causation. |
| LLM summary implementation | WORKING | Structured fact selection, schema validation, malformed/unavailable provider handling and deterministic fallback tests. Actual live incidents used the fallback. |
| External LLM service | UNVERIFIED | Disabled locally; no configured live provider request was made. |
| Alerting | WORKING | Actual generic and Slack-format loopback HTTP receipts, persisted delivery status; timeout/rejection/retry tests and database restart/deduplication tests. New incidents may create new deliveries after recovery/cooldown. |
| External Slack/webhook deployment | UNVERIFIED | Local receiver acceptance does not establish delivery to Slack or another external endpoint. |
| RunbookOS integration | PARTIAL | Existing proposal service, state machine, approval boundary and isolated persistence tests pass; intake is a labeled mock, disabled in the live stack. External contract and execution remain unverified. |
| React dashboard | WORKING | Real overview/hosts/host-detail/predictions/incidents/experiments/models/evaluation views; observed loading and empty states, actual API outage 502 banner with retained data, automatic recovery, narrow-screen host detail, six frontend tests, lint/types/build. |
| Background workers | WORKING | Live feature, observer and frozen inference workers; actual feature/prediction growth, model-loaded metric and persisted observations. Existing workers use Python 3.14. |
| Redis / Celery / Beat | MISSING | Not used by the existing implementation. They were not introduced during verification. |
| Docker | WORKING | Clean-source API/agent/demo/frontend/worker/inference images built and run; Linux CPU inference/training suite passed 46 tests. |
| Docker Compose | WORKING | Isolated stack booted with empty volumes. Repaired an omitted inference startup path with an optional `inference` profile that registers the existing frozen model before starting its worker. |
| Kubernetes / Helm | UNVERIFIED | Local rendering and safety checks passed: 104 resources across three fixtures and six rejected unsafe configurations. No actual cluster deployment. |
| GitHub Actions | UNVERIFIED | Workflow inspected; its local test/build counterparts ran. Frozen staged GitHub import is still in progress, and remote CI acceptance has not been observed. |
| Tests and static checks | WORKING | 223 unique existing tests pass with no skips; Linux repeats 46 ML/inference tests. Ruff, 121-file formatting check, strict configured typing and production frontend build pass. |

The executed local architecture is React → Nginx → FastAPI → PostgreSQL/TimescaleDB; psutil/application exporters → Prometheus → causal feature worker → TimescaleDB → frozen MLP/Isolation Forest inference → persisted predictions/incidents → SHAP/factual summaries → optional webhook deliveries. An independent observer records actual service degradation. Grafana reads Prometheus. No remediation is executed by Sentinel.

See [baseline verification](baseline-verification.md) for commands, measurements, acceptance gates and unresolved limits. `WORKING` never means the experimental model is safe or effective for operational decisions. RunbookOS, live LLM, external alerts, remote CI and remote deployment remain separately unaccepted.
