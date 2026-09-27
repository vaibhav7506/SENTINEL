# Phase 5 — actual offline training and held-out evaluation

The offline training and evaluation pipeline is implemented and verified. **The trained model did not demonstrate useful held-out prediction:** it missed every positive test window at its validation-selected threshold. This completes the requested reproducible evaluation, not production model validation. Work stops before Phase 6.

## Evidence and artifacts

- Dataset: [`1bcd8acdc1fab6f9`](../artifacts/datasets/1bcd8acdc1fab6f9/report.json), 54 real feature windows from `docker-vm`.
- Model: [`20260926T204848-mlp-1bcd8acd`](../artifacts/models/20260926T204848-mlp-1bcd8acd/metadata.json), trained at 2026-09-26 20:49:12 UTC.
- [Complete evaluation](phase-5-evaluation.json), [artifact/database verification](phase-5-validation.json), and [dataset summary](phase-5-dataset.json).
- [Validation campaign](phase-5-usable-validation-campaign.json) and [held-out test campaign](phase-5-final-test-campaign.json): bounded real latency/HTTP-error faults, with objectively observed breach and recovery.
- [Reproduction and implementation details](training.md).

The [split plan](phase-5-split-plan.json) was declared before the fresh validation and test campaigns. Previous collection plans were abandoned after the stack/process interruption without fitting or evaluating a model. Their records are retained. No test outcome was used to select the model, threshold, calibration, or partition boundaries.

## Data and model

| Partition | Non-failure windows | Pre-failure windows | Positive failure type |
| --- | ---: | ---: | --- |
| Train | 6 | 10 | Latency |
| Validation | 17 | 6 | Latency |
| Test | 5 | 10 | HTTP error |

Complete 900-second input windows and 600-second future-label intervals, plus a 15-second confirmation tail, stay strictly inside one chronological partition. Boundary overlap is purged. Failure means three consecutive SLO breaches within 15 seconds, matching the observer. Negative labels require actual probe coverage; isolated spikes do not establish sustained failure. The frozen Phase 4 dataset and its strict default label mode are preserved.

The CPU PyTorch MLP uses 209 inputs, hidden layers of 16 and 8 ReLU units, dropout 0.1, and one logit. Medians, means and scales are fitted only on training rows. Weighted BCE handles the observed class balance. Seed 1729 and deterministic CPU execution are fixed. Training stopped after 31 epochs; epoch 1 had the best unweighted validation loss and its saved checkpoint was restored.

Poor raw validation calibration triggered regularized Platt scaling fitted on validation only. The warning threshold, 0.9875243902206421, maximizes validation F1. Validation is reused for stopping, calibration and threshold selection, so its apparent performance is optimistic. The test calibration warning is retained without test adaptation.

The 200-estimator Isolation Forest is fitted on five training inputs whose complete input probe histories satisfy the SLO. Its unfamiliarity score is stored separately from failure probability. Five overlapping local windows provide very limited coverage of healthy behavior.

## Actual results

| Metric | Validation | Held-out test |
| --- | ---: | ---: |
| Precision | 1.000 | 0.000 |
| Recall | 0.500 | 0.000 |
| F1 | 0.667 | 0.000 |
| PR-AUC (average precision) | 0.615 | 0.668 |
| Random-ranking PR-AUC baseline | 0.261 | 0.667 |
| ROC-AUC | 0.510 | 0.300 |
| TN / FP / FN / TP | 17 / 0 / 3 / 3 | 5 / 0 / 10 / 0 |
| Brier score after calibration | 0.113 | 0.490 |
| Calibration ECE, 10 bins | 0.003 | 0.521 |
| Non-failure scoring exposure | 17 minutes | 5 minutes |
| False warning windows / simulated alert epochs | 0 / 0 | 0 / 0 |
| Earliest useful warning lead | 302.413 seconds | No useful warning |

False-alert rates are zero per non-failure host-hour/day in these tiny offline exposures. **That is not evidence of a low operational false-alert rate:** the test model made no warnings and missed all positive windows. The always-warn test baseline has F1 0.800. Test ranking is essentially at the random average-precision baseline, and calibration does not transfer.

Per-type results cover one eligible latency event in validation and one eligible HTTP-error event in test; their metrics appear separately in the complete report. The second fault in each campaign falls inside recovery exclusion and supplies no independent pre-failure scoring cohort. Abrupt scheduled injections have no guaranteed advance symptoms. The one-host, correlated, small dataset cannot establish fleet accuracy, useful incident prediction, or production readiness. More representative training incidents and a new untouched evaluation cohort are needed before operational use.

## Verification and phase boundary

Artifact verification reloaded weights, the best checkpoint, transforms, calibration and Isolation Forest; reproduced 38 saved scores and all evaluation metrics; checked the validation-only threshold and train-only normalizer; verified all 54 supervised vectors and labels against persisted evidence; and checked all five healthy inputs against raw probe coverage. Artifact and frozen report digests passed. Actual ModelVersion and EvaluationRun metadata match the saved report.

The training contract suite passes 12 tests, including temporal purge, missing/censored label evidence, sustained-failure confirmation, train-only transforms, dataset mutation, and experiment leakage across hosts. The feature worker's early timer wake was fixed without changing feature definitions or the schema hash.

Final checks also passed: 60 backend tests (two optional database tests skipped), Ruff lint and formatting across 64 files, strict mypy across 30 source files, and frontend lint, type checking and production build. Refreshed API and Console containers are healthy. Stack verification passed all nine services and seven health checks; API liveness reports Phase 5. All eight executed experiments are completed, no failure remains unrecovered, and exactly one ModelVersion and one EvaluationRun are stored.

Online predictions, incidents, alerts and remediation proposals remain empty and unimplemented. The Console reports the actual offline outcome; its complete prediction dashboard remains a later phase. ML packages use a separate locked Windows x64 Python 3.14 environment and are not installed in the API container.
