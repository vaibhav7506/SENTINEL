# Phase 4 — controlled experiments and dataset acceptance

**Phase 4 complete, verified locally on 2026-09-26. Stopped before Phase 5.** No model training, evaluation, predictions, alerts, or remediation was performed.

## Files created or modified

Created `demo-service/app/chaos.py` and its safety tests; backend chaos routes and execution policy; `backend/app/workers/observer.py`; migrations `0003_chaos_observations.py` and `0004_probe_criteria.py`; backend label/policy/API and isolated observer database tests; `training/{__init__,labels,build_dataset}.py`; `scripts/{init_chaos_env,run_chaos_campaign,verify_dataset}.py`; `docs/chaos-dataset.md`; this record; the real campaign, dataset, artifact-validation, and ongoing-feature-validation JSON records; and the content-addressed samples/report under `artifacts/datasets/efc993ec38e28ad0`.

Updated backend models/settings, API startup/router registration, readiness revision and health phase, test import setup, the demo `/work` handler, Compose, Prometheus targets, `.env.example`, Makefile, README, dependency notes, the historical Phase 3 record, and minimal Console phase/scope text. The database integration helper now covers the observer too. Feature verification now permits actual Phase 4 experiment/events and writes `docs/feature-validation.json`, preserving historical Phase 3 results. The ignored local `.env` was explicitly configured for bounded, authenticated, allowlisted demo execution. No dependencies or lockfiles changed. The original poller and data are preserved.

## Real campaign and dataset

The campaign required **690.014516 seconds of uninterrupted healthy coverage from 337 real probes**, then ran two 45-second faults against only the local demo `/work` route:

| Experiment | Actual observed degradation | Recovery |
| --- | --- | --- |
| Application latency, 750 ms injection | Three consecutive responses exceeding the 0.5-second SLO | Three consecutive healthy probes confirmed |
| HTTP error injection | Three consecutive actual HTTP 503 responses | Three consecutive healthy probes confirmed |

Both experiments have persisted start/end, target, parameters, expected criterion, and observed event association. Both FailureEvents have observed onset, confirmation, probe evidence, and observed recovery. Experiment requests alone did not create failure labels. The recorded evidence is in [phase-4-campaign.json](phase-4-campaign.json). Deployment gaps interrupted the initial baseline; the final accepted baseline used only its subsequent uninterrupted healthy segment.

The real dataset contains **11 samples: 10 positive and 1 negative**, using the default **600-second horizon**, host `docker-vm`, feature-window end times **15:31–15:41 UTC on 2026-09-26**, and the existing ordered **209-feature** schema. Windows for the Windows host have no service-probe coverage and were not assigned healthy labels.

The report records two observed failure types: one latency SLO breach and one HTTP error SLO breach. All positive sample labels refer to the closest first (latency) failure. The following error experiment does not create an independent positive cohort because those windows already fall inside the first failure/recovery exclusion. This distinction is explicitly recorded as observed versus positive-associated failure types.

Unobserved/gapped horizons, right-censored rows, and active-failure/recovery windows were excluded. Per-column missing counts/fractions are recorded without imputation. The two close experiments and their overlapping windows merge into **one group**, so all 11 samples remain in train and split status is **`insufficient_independent_groups`**. No held-out performance is claimed. More independent campaigns are required before defensible Phase 5 evaluation.

Dataset files are [samples.jsonl](../artifacts/datasets/efc993ec38e28ad0/samples.jsonl) and [report.json](../artifacts/datasets/efc993ec38e28ad0/report.json), with summary [phase-4-dataset.json](phase-4-dataset.json).

## Tests and checks

- Backend ordinary run: **58 tests passed**, with two opt-in database tests skipped.
- Isolated database run: **2 tests passed**, covering immutable feature retries/rollback and actual persisted three-probe confirmation/recovery/experiment completion using separate HTTP fixtures. Migration upgrade, downgrade to the extension revision, re-upgrade, and schema-parity checks passed. Temporary databases were removed.
- Demo: **9 tests passed**, including authentication, independent allowlists, production denial even when allowlisted, bounded expiry, cancellation, healthy-route isolation, and existing work/metrics behavior.
- Agent: **12 tests passed**; original prototype: **18 tests passed**. **99 tests passed overall across ordinary and opt-in runs.**
- Ruff lint/format passed for 56 backend/demo/training/helper files; strict mypy passed for backend plus training (29 files) and demo (4 files). Frontend lint, TypeScript checks, production build, and final Compose image build/startup passed.
- Real artifact verification confirmed its SHA-256 file hash, exact feature-vector/database correspondence, recomputed labels from actual events/probes, experiment split isolation, and no overlapping train/test intervals. The latter is an invariant check, not evidence of a held-out set in this one-group dataset.
- Live unauthorized, production-namespace, and unsupported-extra-field requests were rejected without starting faults. The injector reported inactive, and normal demo work returned HTTP 200.
- All **nine services** run, all **seven configured health/readiness probes** pass, and both workers run. HTTP health, readiness, frontend proxy, Prometheus, Grafana, and demo checks passed.
- Ongoing feature acceptance observed growth from **110 to 112 windows in 75 seconds** after the final restart. Both hosts retained sane bounded feature vectors and timestamps. The database contained two experiments and two FailureEvents; inference/incident/alert/model/evaluation/remediation tables remained empty.

Machine-checkable verification results are in [phase-4-validation.json](phase-4-validation.json) and [feature-validation.json](feature-validation.json).

## Limits and next phase

The injector supports only bounded latency and HTTP errors in this single-process local demo. CPU/memory stress, process/pod termination, network delay, Kubernetes execution, and distributed observer/injector coordination are not implemented. Explicit scope and token gates remain required.

Healthy labels are supported by sampled future probes, not proof of continuous availability. The dataset is small and imbalanced, with one independent group and one positive-associated failure family. Abrupt synthetic faults do not establish that healthy telemetry predicts failures in advance. Further independent evidence is needed for training/evaluation claims. Missing values stay null. See [chaos-dataset.md](chaos-dataset.md) for exact semantics, settings, commands, and exclusions.
