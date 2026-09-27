# Phase 3 feature contract

The `app.workers.features` process queries Prometheus every 60 seconds and writes derived windows to TimescaleDB. The default observation window is 900 seconds. Window ends align to UTC epoch multiples of the interval; one timestamp is shared by all queries in a cycle. Slow cycles skip missed intervals rather than inventing observations. Restarting does not backfill downtime.

## Causality and telemetry scope

Host range selectors use `/api/v1/query` at time T with `[900s]`, preserving actual scrape timestamps. Host features use samples in `(T-900,T]`; future samples, NaN, and infinity are excluded. Equal duplicate samples are deduplicated; conflicting timestamps and ambiguous exporters are rejected. CPU is required for host discovery. Hosts and optional host metrics require a last sample within 90 seconds of T. Optional query failures are recorded in quality metadata and leave missing feature values.

Application features use causal `/api/v1/query_range` evaluations every 15 seconds ending at T. Request and error rates are summed `rate(counter[60s])`, in requests/errors per second, across the explicitly mapped Prometheus job. Error rate is a count per second, not an error fraction. Latency is the histogram-estimated p95 in seconds across that job. Each evaluation's 60-second lookback can precede the observation window start, but never T. No future offsets or look-ahead fills are used. Absent error counters stay missing; they are not replaced with zero. An idle histogram may yield NaN, which stays missing.

`FEATURE_HOST_SERVICE_MAP` is a JSON host-id to job mapping. Compose defaults to `{"docker-vm":"demo-service"}`. The Windows host receives no demo-service association. Native workers default to an empty mapping; set it explicitly when appropriate. The Linux container agent retains the telemetry scope described in [telemetry.md](telemetry.md). A mapped job may aggregate multiple service instances; it must actually belong to that host for cross signals to be meaningful.

## Schema and statistics

`app.features.schema.FeatureSchema` is the shared contract for future training and inference. Version `v1-w900-e60-r300` defines 209 ordered features: 14 host metrics and 3 application metrics, each with 12 statistics, followed by 5 cross signals. Names, duration parameters, and statistic definitions contribute to the SHA-256 schema hash. Future semantic changes require a new version. Every database row stores the version, hash, ordered names, values, named feature dictionary, and sample quality.

| Statistic | Definition |
| --- | --- |
| latest / mean / median / min / max | Observed values in the window |
| std | Population standard deviation |
| delta | Last minus first; requires two samples |
| percentage_change | `100 * delta / abs(first)`; missing when first is zero |
| linear_trend | Ordinary least-squares slope using actual elapsed seconds |
| ewma | Time-aware exponential smoothing, 60-second half-life, seeded with the first sample |
| rolling_variance | Population variance of observed values in the most recent 300 seconds |
| rate_of_change | Endpoint delta divided by elapsed seconds |

Slopes and rate-of-change are in the metric's units per second. Single-sample windows have descriptive values, but no delta, percentage change, slope, or rate of change. Missing values are JSON null, not imputed zeros. Short history is permitted and visible through counts, timestamps, observed duration, and age for every metric. There is no minimum-history eligibility gate for model inference yet.

The cross features are CPU slope × latency slope, memory percentage slope × latency slope, error-count-rate slope × latest latency, summed disk read/write throughput × latest latency, and summed network throughput rate-of-change × latest error-count rate. All operands must exist. Disk throughput is a proxy interaction; it does not measure I/O utilization, queue depth, or saturation. These are candidate features, not causal diagnoses or validated predictors.

## Persistence and lifecycle

The migration creates Host, FeatureWindow, FailureEvent, Prediction, Incident, Alert, ChaosExperiment, ModelVersion, EvaluationRun, and RemediationProposal tables. Only hosts and feature windows are populated in Phase 3. No labels, model artifacts, predictions, incidents, alerts, chaos experiments, or remediation actions are fabricated.

`feature_windows` is a TimescaleDB hypertable partitioned on `window_end`. Its composite primary key includes host, end timestamp, and schema version. Host upsert and window insert share a transaction. Repeated windows use `ON CONFLICT DO NOTHING`, retaining the original feature vector. Failed transactions roll back their host updates. TimescaleDB creates its time index; model metadata includes it. Raw samples remain in Prometheus.

Prometheus requests are bounded by a configured timeout and a concurrency limit of four. Host discovery is capped at 1,000 eligible hosts. Database connection/pool waits are bounded. This sequential per-host writer is suitable for the local demonstration; distributed worker coordination, retention policy, and fleet-scale tuning are future work. Counters reflect commits by this process and reset on restart. Last-success means an eligible-host cycle completed without transaction failure; optional query errors remain recorded on each window.

## Configuration and verification

| Setting | Default |
| --- | --- |
| FEATURE_INTERVAL_SECONDS | 60 |
| FEATURE_WINDOW_SECONDS | 900 |
| FEATURE_FRESHNESS_SECONDS | 90 |
| FEATURE_QUERY_TIMEOUT_SECONDS | 15 |
| FEATURE_HOST_SERVICE_MAP | Empty natively; docker-vm/demo-service in Compose |

Run `python -m app.workers.features --once` from the backend environment to process a single aligned window. The command returns nonzero if no eligible-host cycle succeeds. The regular Compose worker keeps running. Unit tests cover deterministic ordering, known trends, irregular timestamps, missing signals, exporter ambiguity, future-data leakage, and bounded query timestamps. `scripts/check_feature_database.py` tests real migration upgrades/downgrades, schema parity, immutable retries, and rollback in a temporary database. `scripts/verify_features.py` is read-only against the demo database and observes real window growth for 75 seconds.
