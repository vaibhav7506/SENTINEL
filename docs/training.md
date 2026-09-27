# Offline model training and evaluation

The Phase 5 pipeline consumes persisted `FeatureWindow` vectors and objective future failure labels. It refuses missing train, validation or test partitions, missing classes, altered JSONL bytes, overlapping input/label intervals, and shared experiments across partitions. A JSON split plan is declared before held-out collection. The complete `(window_start, window_end + horizon + 15-second confirmation tail)` interval must fit strictly within a chronological partition. Boundary windows are purged. Minute rows remain correlated within each partition.

## Reproduction on this workstation

Training uses a separate Windows x64 Python 3.14 environment, avoiding PyTorch and numerical libraries in the API container. `training/uv.lock` pins the official PyTorch CPU wheel, scikit-learn, NumPy and joblib. Use `uv sync --project training --locked`, then run commands from the workspace root:

```powershell
uv run --project training python -m training.build_dataset --split-plan docs/phase-5-split-plan.json --summary docs/phase-5-dataset.json
uv run --project training python -m training.train artifacts/datasets/<dataset-id>
uv run --project training python scripts/verify_training.py artifacts/models/<version>
uv run --project training python -m pytest training/tests -q
uv run --project training mypy --config-file training/pyproject.toml backend/app training/labels.py training/build_dataset.py training/runtime.py training/train.py
```

The saved dataset is a frozen snapshot; rebuilding it after further observations may produce another digest. Use the dataset path in model metadata to reproduce that exact experiment. The feature order and schema hash are stored with the model. Source hashes identify the uncommitted implementation alongside the available Git HEAD.

## Supervised probability

The MLP has 209 inputs, hidden layers of 16 and 8 ReLU units, dropout 0.1, and one logit. A sigmoid, followed by saved validation-fitted calibration when triggered, returns `P(failure within 600 seconds)` as an empirical model estimate. This estimate has only the calibration support reported by the evaluation; it is not a guarantee.

Missing features use medians fitted only on training data. Columns wholly absent in training use zero. Means and standard deviations also use training data; constant columns receive scale one. AdamW uses learning rate 0.001 and weight decay 0.01. Positive BCE weight is non-failure/failure training count. Seed 1729, deterministic CPU algorithms and one Torch thread are fixed. Unweighted validation BCE selects the best checkpoint; training stops after 30 epochs without improvement, capped at 500 epochs. The best checkpoint is saved on every improvement and restored before scoring.

Raw validation Brier score and ten-bin expected calibration error are recorded. Validation ECE above 0.1 or Brier worse than the training-prevalence baseline triggers regularized Platt scaling of validation logits. Validation F1 chooses the warning threshold, with ties selecting the larger threshold. Validation is reused for stopping, calibration and threshold selection, so its metrics are optimistic. Model weights, transforms, calibration and threshold are fixed before test inference. Test outcomes never retune them.

## Unfamiliarity

Isolation Forest uses only training input windows with complete actual healthy probe coverage after the same training-fitted transform, 200 estimators, seed 1729 and automatic contamination. Its score is the negative decision function: larger scores indicate unfamiliar patterns. It is stored separately from failure probability and does not mean that an imminent failure is confirmed. No combined score is produced. Input health differs from the supervised future-horizon label: an entirely healthy input may have an unlabeled future horizon containing an isolated latency spike. Such inputs are retained separately in `healthy_training.jsonl`; future-positive windows are excluded. The auxiliary file has its own SHA-256, and the composite dataset digest covers both files. The complete input plus future and confirmation interval still lies entirely inside the training partition.

## Report definitions

`metadata.json` records precision, recall, F1, average precision (PR-AUC), ROC-AUC and a TN/FP/FN/TP confusion matrix for validation and test. It also contains Brier score, calibration bins, ECE, eligible per-failure-type recall/precision, and per-event lead time. The report also includes always-warn and random-ranking baselines. A useful warning must be at least 60 seconds before actual observed failure onset and no more than the prediction horizon. Events without eligible pre-failure windows are outside the evaluated lead-time cohort.

False warning windows are counted directly. Offline alert epochs simulate a five-minute per-host cooldown; their rates use one minute of non-failure exposure per eligible negative feature row. Gaps and censored/recovery periods contribute no exposure. Rates per host-day are extrapolations of this short non-failure scoring exposure, not a day of continuous operational evidence. Stored offline score rows contain separate `failure_probability` and `anomaly_score` fields.

Abrupt scheduled fault injections do not necessarily contain advance symptoms. A warning before such an injection may be coincidental. Small local single-host cohorts cannot establish fleet accuracy or production readiness. Poor held-out results remain in the report without tuning against them.

## Artifacts and persistence

Each timestamped model version contains `mlp.pt`, `best_checkpoint.pt`, `isolation_forest.joblib`, `normalizer.json`, `calibration.json`, `history.json`, `scores.jsonl`, and `metadata.json`. Artifact SHA-256 values, dataset digest, source hashes, training time, dependencies, seed, schema, threshold, split and metrics accompany the version. PyTorch loading uses `weights_only=True`. Joblib files must only be loaded from trusted local training outputs.

The supervised sample digest and the healthy-input digest are stored separately. The Phase 5 dataset digest is SHA-256 of supervised JSONL bytes, the literal `\nHEALTHY_TRAINING\n` delimiter, healthy-training JSONL bytes, the `\nIDENTITY\n` delimiter, and the saved canonical identity JSON. Identity includes schema, label criteria, horizon, confirmation tail and the predeclared split plan. Reports are immutable within that content-addressed directory; the model also records the report file digest.

One `ModelVersion` and its `EvaluationRun` are registered after successful training. The verifier reloads both models and transforms, reproduces saved scores and evaluation metrics, checks the best checkpoint, and recomputes the validation threshold and training-only normalizer. It matches database metadata, verifies score-row identities and warnings, and rechecks every supervised vector and label against actual persisted evidence. It also checks Isolation Forest inputs against complete healthy probe coverage. Model metadata records positive failure types separately for each partition. Online predictions, incidents, alert delivery and remediation remain for subsequent phases.

## Collecting another independent dataset

With the local stack healthy and allowlisted demo chaos enabled, `uv run --project backend python scripts/collect_training_evidence.py` declares a new timestamped split plan and collects train, validation and test campaigns. Under the default window/horizon this takes about two hours. Each campaign first requires uninterrupted actual healthy probe coverage, so gaps can delay it. The Windows collection thread requests temporary sleep prevention and releases it on exit; no persistent power setting changes. Build the dataset with the printed `artifacts/campaigns/<timestamp>/plan.json` path. If delays leave a partition without both classes, training refuses it; collect a new predeclared cohort rather than relocating windows after viewing test results.

## Meaningful degradation and negative labels

The observer confirms meaningful degradation using three consecutive SLO breaches within 15 seconds. Phase 5 future labels use this same sustained definition: isolated latency spikes do not establish that failure occurred. Negative labels require complete actual probe coverage through the horizon and an additional 15-second confirmation tail. An unconfirmed three-probe burst, a coverage gap, a changed criterion, active failure, recovery exclusion, or insufficient confirmation time remains unlabeled. The split embargo includes the confirmation tail. The default Phase 4 strict all-probe-health labeling remains available for its historical artifacts.

Isolation Forest input health remains stricter: every probe across its entire input window must satisfy the SLO. Its healthy-input file and verification do not reinterpret isolated input breaches as healthy.

The feature worker also handles an early timer wake by waiting for a genuinely new aligned tick. Previously, a wake just before the next minute could recompute the previous immutable window and then miss the next cycle. This fix changes scheduling only; feature definitions and their schema hash remain unchanged.
