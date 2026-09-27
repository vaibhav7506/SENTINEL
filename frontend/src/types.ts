export type Json = Record<string, unknown>
export interface Prediction {
  id: string; host_id: string; model_version_id: string; predicted_at: string
  feature_window_end: string; probability: number; anomaly_score: number | null
  horizon_seconds: number; schema_version: string
  explanation: Json & { top_drivers?: Driver[]; forecast_valid_until?: string }
}
export interface Driver { feature: string; contribution: number; value: number | null }
export interface Host {
  id: string; name: string; environment: string; service_job: string | null; last_seen: string
  status: string; high_risk: boolean; prediction_fresh: boolean; prediction_age_seconds: number | null
  threshold: number | null; latest_prediction: Prediction | null
  observation: { observed_at: string; breached: boolean; latency_seconds: number; outcome: string } | null
}
export interface Model {
  id: string; version: string; created_at: string; threshold: number
  schema_version: string; schema_hash: string; training_metadata: Json
}
export interface Incident {
  id: string; host_id: string; status: string; prediction_id: string | null; failure_event_id: string | null
  predicted_at: string | null; observed_at: string | null; confirmed_at: string | null
  resolved_at: string | null; summary: Json
  prediction: Prediction | null; failure_event: Failure | null; alerts: Alert[]; proposals: Proposal[]
}
export interface Failure {
  id: string; host_id: string; failure_type: string; observed_at: string
  confirmed_at: string | null; recovered_at: string | null
}
export interface Experiment {
  id: string; host_id: string; failure_type: string; started_at: string; ended_at: string | null
  status: string; coverage: string; eligible_scores: number
  warning_lead_seconds: number | null; alert_lead_seconds: number | null
  failure_event: Failure | null; expected_degradation: Json; observed_degradation: Json | null
}
export interface Alert {
  id: string; incident_id: string; channel: string; status: string; delivered_at: string | null
  attempt_count: number; last_error: string | null
}
export interface Proposal {
  id: string; incident_id: string; suggested_action: string; adapter_kind: string
  reason: string; submission_status: string; approval_status: string; execution_status: string
}
export interface Metric {
  precision: number; recall: number; f1: number; pr_auc: number; samples: number
  confusion_matrix: Record<string, number>; non_failure_scoring_host_hours: number
  false_alerts_per_non_failure_host_hour: number; exposure_policy: string
  classification_baselines: { random_ranking_pr_auc: number }
  lead_time_by_event: { event_id: string; lead_seconds: number | null; eligible_pre_failure_windows: number }[]
  per_failure_type: Record<string, { recall: number; f1: number; pr_auc: number; positive_windows: number; true_positive_windows: number; precision_against_shared_healthy_baseline: number }>
  calibration: { brier_score: number; ece_10_bins: number; bins: { lower: number; count: number; mean_probability: number; observed_rate: number }[] }
}
export interface Evaluation {
  id: string; model_version_id: string | null; dataset_reference: string; completed_at: string | null
  metrics: Partial<Record<'test' | 'validation', Metric>>; split_definition: Json
}
export interface Snapshot {
  as_of: string; limit: number; host_limit: number; totals: Record<string, number>; hosts: Host[]
  overview: { monitored_hosts: number; healthy_hosts: number; high_risk_hosts: number
    active_incidents: number; alerts_last_24h: number; current_model: Model | null }
  predictions: Prediction[]; incidents: Incident[]; models: Model[]; experiments: Experiment[]
  alerts: Alert[]; proposals: Proposal[]; evaluations: Evaluation[]; failure_events: Failure[]
  governance: { enabled: boolean; adapter: string; human_approval_required: boolean; execution_owner: string }
}
export interface HostDetail {
  as_of: string; host_id: string; predictions: Prediction[]; incidents: Incident[]
  telemetry: { status: string; sample_age_seconds: number | null; window_seconds: number
    series: { metric: string; values: [number, number][] }[] }
}
