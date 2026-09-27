# Controlled demo chaos and evidence-based labels

Phase 4 implements bounded application latency and HTTP 503 injection in the local demo service. It does not implement CPU/memory stress, process termination, network manipulation, or Kubernetes chaos. Unsupported types, targets, namespaces, and extra request fields are rejected. There is no arbitrary shell, URL, container, pod, or process selector.

## Gates and bounded cleanup

Execution requires `CHAOS_ENABLED=true`, a development/test/demo environment explicitly present in `CHAOS_ALLOWED_ENVIRONMENTS`, `demo-service` in `CHAOS_ALLOWED_TARGETS`, and `sentinel-demo` in `CHAOS_ALLOWED_NAMESPACES`. `sentinel-demo` is the local scope name; it is not a claim of Kubernetes execution. The persisted `docker-vm` identity must belong to the configured environment and map to the demo-service Prometheus job. Production is rejected even if somebody adds it to the allowlist.

The API and demo injector independently check their gates. Control calls require a generated Bearer token of at least 32 characters. Tokens remain in ignored `.env`; helpers do not print them. The only allowed injector addresses are the Compose demo service and its fixed loopback development endpoint. Read-only experiment/event routes are available locally without the control token; execution and cancellation require it.

Each fault lasts 10–120 seconds; injected latency is capped at 2 seconds. Only `/work` is affected. `/health`, metrics, and control stay reachable. An in-process monotonic deadline removes the fault even if the API, campaign, or observer stops. Only one fault can be active; concurrent starts are rejected. Cancellation clears it immediately, and a demo process restart clears all fault state. This is a single-process local injector; replicas and production deployment are unsupported.

Every valid execution attempt is stored before calling the injector, including target, host, namespace, parameters, expected criteria, and start time. An unconfirmed control response is recorded as `control_unknown`, never as a confirmed degradation. The observer reconciles the actual injector ID and status. End time records when inactivity was observed, which may be later than the monotonic expiry; the reported expiry is also retained. Failed/no-effect experiments have no fabricated FailureEvent.

## Objective degradation and recovery

The independent observer requests `/work?units=100000` roughly every 2 seconds, with a 3-second timeout. It records request start, response/timeout observation time, monotonic elapsed duration, status code, outcome, current criteria, and any active experiment association. These are validation observations, not copies of raw Prometheus samples.

A healthy probe returns HTTP 200 within the configured latency SLO, default 0.5 seconds. Other responses, 5xx errors, connection/timeouts, and SLO breaches are degraded observations. **Three consecutive degraded probes within 15 seconds** create a FailureEvent. Its onset is the first observed breach; its confirmation time is the third. The criterion and three probe IDs, statuses, and durations are stored as evidence. A single spike produces no confirmed failure event. CPU reaching a high percentage does not create an event.

Recovery requires three consecutive healthy probes within 15 seconds. The first healthy probe supplies `recovered_at`, with separate recovery-confirmation evidence. Observer restart uses persisted probes and open events rather than resetting the event history. The implementation assumes one observer per monitored demo host; distributed deduplication is future work. A monitoring gap cannot prove health.

## Labels and schema

The default prediction horizon is **600 seconds**. For a confirmed failure onset F, a window ending at T receives 1 only when `T < F <= T + horizon`. The closest qualifying failure is the primary label source, and confirmed failures must be visible in the dataset snapshot. A window ending at the onset is already degraded and is excluded.

A negative label requires the entire future horizon to have actual healthy probe coverage, including probes bracketing both endpoints within 10 seconds and no gap over 10 seconds. Actual response status and elapsed latency are re-evaluated under the dataset's explicit SLO; absence of a FailureEvent alone is insufficient. A breached but unconfirmed probe excludes the row. Incomplete/right-censored horizons stay unlabeled. This is sampled service-health evidence, not continuous proof that every request succeeded.

Active failure and recovery windows are excluded. The default recovery exclusion is 900 seconds, matching the feature observation window, to avoid immediately training on post-failure history. Changing this interval affects the label contract and must be reported. Windows with mismatched schema/hash/order, non-finite features, insufficient CPU history (less than 80% observed span), or unavailable future coverage are excluded. Future timestamps in persisted feature quality cause an error. Optional feature values stay null; no imputation or fitting occurs in this phase.

The output contains only the ordered FeatureSchema values as model inputs. Future event IDs/times, experiment association, labels, and split metadata are separate from the feature vector. The database is read through a repeatable-read, read-only snapshot and the report records its cutoff.

## Splits and small-data limitations

Overlapping experiment influence intervals are merged. Windows linking multiple experiments join their groups. Later groups are held out chronologically; training rows whose observation-plus-label intervals overlap held-out rows for the same host are purged. No random row splitting is used. Related experiment windows cannot appear in both train and test.

When only one independent group exists, all eligible rows remain in train and the report says `insufficient_independent_groups`. This is deliberate: a small local campaign cannot justify held-out evaluation. Collect additional campaigns separated by feature/recovery/horizon spans, or on genuinely independent monitored hosts, before Phase 5 performance evaluation. A dataset with both classes is useful for pipeline validation, not proof of predictive quality.

## Commands

From the repository root with Docker Desktop running:

```sh
python scripts/init_chaos_env.py --enable-local-demo
docker compose up --build -d
uv run --project backend python scripts/run_chaos_campaign.py
uv run --project backend python -m training.build_dataset
```

The explicit initialization step enables only the local allowlisted demo and generates a private token. The campaign waits for at least horizon + 90 seconds of uninterrupted actual healthy coverage before executing two 45-second experiments. It resets its baseline segment after a probe gap or breach. It waits for actual confirmed degradation and recovery, then records `docs/phase-4-campaign.json`. Fault TTLs apply if the helper is interrupted.

The builder writes `artifacts/datasets/<content-hash>/samples.jsonl` and `report.json`, with a summary copy at `docs/phase-4-dataset.json`. It exits nonzero if either class is absent. The report contains sample counts, label distribution, observed and positive-associated failure types, hosts, time span, per-column missing counts/fractions, excluded-window reasons, schema, horizon, and split status. The SHA-256 digest covers the samples file; row metadata retains the horizon and provenance. Run with `--horizon-seconds 120` only when an explicitly shorter objective is desired; the artifact records the actual horizon rather than implying ten-minute labels.

Relevant settings are `CHAOS_*`, `OBSERVATION_INTERVAL_SECONDS` (1–3), `OBSERVATION_LATENCY_SLO_SECONDS`, `PREDICTION_HORIZON_SECONDS`, and `DATASET_RECOVERY_EXCLUSION_SECONDS`. Set `CHAOS_ENABLED=false` and recreate affected services to disable future executions. Existing in-process faults always expire within their original bound. The observer and feature worker can remain running with chaos disabled.
