"""Fresh immutable features -> frozen models -> durable incidents and alerts."""

import argparse
import asyncio
import json
import logging
import math
import signal
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.models import (
    Alert,
    ChaosExperiment,
    FailureEvent,
    FeatureWindow,
    Host,
    Incident,
    ModelVersion,
    Prediction,
)
from app.services.proposals import ProposalService
from inference.alerts import deliver, validate_destination
from inference.scoring import ROOT, Scorer
from inference.summaries import HTTPProvider, summarize

logger = logging.getLogger("sentinel.inference")
LOCK = 736284061


def eligible(window: FeatureWindow, metadata: dict[str, Any], now: datetime, max_age: int) -> bool:
    try:
        return valid_window(window, metadata, now, max_age)
    except KeyError, TypeError, AttributeError, ValueError, OverflowError:
        # A corrupt record cannot stop scoring other eligible hosts.
        return False


def valid_window(
    window: FeatureWindow, metadata: dict[str, Any], now: datetime, max_age: int
) -> bool:
    schema = metadata["feature_schema"]
    if not 0 <= (now - window.window_end).total_seconds() <= max_age:
        return False
    if (
        window.schema_version != schema["version"]
        or window.schema_hash != schema["hash"]
        or window.feature_names != schema["names"]
    ):
        return False
    if window.feature_values != [window.features.get(name) for name in schema["names"]]:
        return False
    quality = window.quality.get("metrics", {})
    cpu = quality.get("cpu_usage_percent", {})
    coverage = cpu.get("coverage_seconds", 0)
    if (
        not math.isfinite(coverage)
        or not 0.8 * schema["window_seconds"] <= coverage <= schema["window_seconds"]
    ):
        return False
    for metric in quality.values():
        if (
            metric.get("count", 0)
            and not window.window_start.timestamp()
            < metric["first_timestamp"]
            <= metric["last_timestamp"]
            <= window.window_end.timestamp()
        ):
            return False
    return bool(
        (window.window_end - window.window_start).total_seconds() == schema["window_seconds"]
    )


def alert_timing(
    delivered_at: datetime | None, onset: datetime, valid_until: datetime
) -> dict[str, Any]:
    if delivered_at is None:
        return {"status": "not_delivered", "lead_seconds": None}
    if delivered_at >= onset:
        return {"status": "after_degradation", "lead_seconds": None}
    if onset > valid_until:
        return {"status": "outside_forecast_horizon", "lead_seconds": None}
    return {
        "status": "before_degradation",
        "lead_seconds": (onset - delivered_at).total_seconds(),
    }


class Worker:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        scorer: Scorer,
        client: httpx.AsyncClient,
        registry: CollectorRegistry,
    ):
        self.settings, self.engine, self.scorer, self.client = (
            settings,
            engine,
            scorer,
            client,
        )
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.proposals = ProposalService(settings, engine)
        self.proposal_submissions = Counter(
            "sentinel_proposal_submissions",
            "Mock proposal intake acknowledgements",
            registry=registry,
        )
        self.proposal_errors = Counter(
            "sentinel_proposal_errors",
            "Optional proposal cycle failures",
            registry=registry,
        )
        self.destinations = {
            channel: secret.get_secret_value()
            for channel, secret in (
                ("generic", settings.alert_generic_webhook_url),
                ("slack", settings.alert_slack_webhook_url),
            )
            if secret.get_secret_value()
        }
        for url in self.destinations.values():
            validate_destination(url)
        self.provider = HTTPProvider(settings, client) if settings.llm_provider == "http" else None
        self.heartbeat = Gauge(
            "sentinel_inference_heartbeat_timestamp_seconds",
            "Live inference heartbeat",
            registry=registry,
        )
        self.success = Gauge(
            "sentinel_inference_last_success_timestamp_seconds",
            "Last scored fresh window",
            registry=registry,
        )
        self.predictions = Counter(
            "sentinel_predictions", "Persisted online scores", registry=registry
        )
        self.high_risk = Counter(
            "sentinel_high_risk_predictions",
            "Committed scores at or above cutoff",
            registry=registry,
        )
        self.alerts = Counter("sentinel_alerts", "Committed queued alerts", registry=registry)
        self.alert_failures = Counter(
            "sentinel_alert_failures",
            "Unsuccessful delivery attempts",
            registry=registry,
        )
        self.duration = Histogram(
            "sentinel_inference_duration_seconds",
            "Scoring including requested SHAP",
            registry=registry,
        )
        self.llm_requests = Counter(
            "sentinel_llm_requests", "LLM selection attempts", registry=registry
        )
        self.llm_failures = Counter(
            "sentinel_llm_failures", "Rejected or failed selections", registry=registry
        )
        self.worker_success = Gauge(
            "sentinel_worker_last_success_timestamp",
            "Last successful cycle with fresh scores",
            ["worker"],
            registry=registry,
        ).labels("inference")
        self.errors = Counter(
            "sentinel_inference_errors", "Inference cycle failures", registry=registry
        )
        self.explanations = Counter(
            "sentinel_shap_explanations",
            "Threshold-crossing SHAP computations",
            registry=registry,
        )
        self.delivered = Counter(
            "sentinel_alert_deliveries",
            "Receiver-accepted alerts",
            ["channel"],
            registry=registry,
        )

    async def initialize(self) -> None:
        async with self.sessions() as session, session.begin():
            model = await session.scalar(
                select(ModelVersion).where(ModelVersion.version == self.scorer.metadata["version"])
            )
            if model is None or model.training_metadata != self.scorer.metadata:
                raise ValueError("Model is not registered with matching frozen metadata")
            self.model_id = model.id
            await session.execute(
                update(Alert)
                .where(Alert.status == "sending")
                .values(
                    status="unknown",
                    last_error="Worker interrupted during delivery; outcome unknown",
                )
            )
        await self.proposals.initialize()

    async def correlate(self) -> None:
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            incidents = (
                await session.scalars(select(Incident).where(Incident.resolved_at.is_(None)))
            ).all()
            for incident in incidents:
                if incident.predicted_at is None or incident.model_version_id != self.model_id:
                    continue
                event = (
                    await session.get(FailureEvent, incident.failure_event_id)
                    if incident.failure_event_id
                    else None
                )
                if event is None:
                    candidates = (
                        await session.scalars(
                            select(FailureEvent)
                            .where(
                                FailureEvent.host_id == incident.host_id,
                                FailureEvent.confirmed_at.is_not(None),
                                FailureEvent.observed_at
                                >= incident.predicted_at
                                - timedelta(seconds=self.scorer.metadata["horizon_seconds"]),
                            )
                            .order_by(FailureEvent.observed_at)
                        )
                    ).all()
                    for candidate in candidates:
                        predictions = (
                            await session.scalars(
                                select(Prediction)
                                .where(
                                    Prediction.host_id == incident.host_id,
                                    Prediction.model_version_id == incident.model_version_id,
                                    Prediction.probability >= self.scorer.metadata["threshold"],
                                    Prediction.feature_window_end
                                    >= candidate.observed_at
                                    - timedelta(seconds=self.scorer.metadata["horizon_seconds"]),
                                    Prediction.feature_window_end < candidate.observed_at,
                                )
                                .order_by(Prediction.predicted_at)
                            )
                        ).all()
                        matched = next(
                            (
                                p
                                for p in predictions
                                if p.explanation.get("incident_id") == str(incident.id)
                            ),
                            None,
                        )
                        if (
                            matched is None
                            and candidate.observed_at <= incident.predicted_at
                            and (
                                candidate.recovered_at is None
                                or candidate.recovered_at >= incident.predicted_at
                            )
                        ):
                            matched = await session.get(Prediction, incident.prediction_id)
                        if matched is not None:
                            event = candidate
                            incident.failure_event_id = event.id
                            incident.observed_at, incident.confirmed_at = (
                                event.observed_at,
                                event.confirmed_at,
                            )
                            incident.summary = {
                                **incident.summary,
                                "matched_prediction_id": str(matched.id),
                                "prediction_lead_seconds": (
                                    event.observed_at - matched.predicted_at
                                ).total_seconds()
                                if matched.predicted_at < event.observed_at
                                else None,
                            }
                            break
                if event is not None:
                    alerts = (
                        await session.scalars(select(Alert).where(Alert.incident_id == incident.id))
                    ).all()
                    timing = {
                        str(alert.id): alert_timing(
                            alert.delivered_at,
                            event.observed_at,
                            datetime.fromisoformat(alert.payload["forecast_valid_until"]),
                        )
                        for alert in alerts
                    }
                    incident.summary = {
                        **incident.summary,
                        "alert_timing": timing,
                        "actual_failure_onset": event.observed_at.isoformat(),
                    }
                    incident.status = "observed"
                    if event.recovered_at is not None:
                        incident.status, incident.resolved_at = (
                            "recovered",
                            event.recovered_at,
                        )
                else:
                    recent = (
                        await session.scalars(
                            select(Prediction)
                            .where(
                                Prediction.host_id == incident.host_id,
                                Prediction.model_version_id == incident.model_version_id,
                                Prediction.predicted_at >= incident.predicted_at,
                            )
                            .order_by(Prediction.feature_window_end.desc())
                            .limit(3)
                        )
                    ).all()
                    if (
                        len(recent) == 3
                        and all(p.probability < self.scorer.metadata["threshold"] for p in recent)
                        and (
                            recent[0].feature_window_end - recent[-1].feature_window_end
                        ).total_seconds()
                        <= 180
                    ):
                        incident.status, incident.resolved_at = "risk_cleared", now
                    elif now > datetime.fromisoformat(
                        incident.summary["forecast_valid_until"]
                    ) + timedelta(seconds=15):
                        incident.status = "forecast_expired_unconfirmed"
                        # A disappeared host is unknown, and the old forecast must not
                        # suppress a fresh condition after telemetry returns.
                        incident.resolved_at = now
                if incident.resolved_at is not None:
                    await session.execute(
                        update(Alert)
                        .where(
                            Alert.incident_id == incident.id,
                            Alert.status.in_(["pending", "retry"]),
                        )
                        .values(
                            status="cancelled",
                            last_error="Condition resolved before delivery",
                        )
                    )

    async def score_windows(self) -> int:
        count = 0
        high_count = 0
        queued_count = 0
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            windows = (
                await session.scalars(
                    select(FeatureWindow)
                    .where(
                        FeatureWindow.window_end <= now,
                        FeatureWindow.window_end
                        >= now - timedelta(seconds=self.settings.inference_max_age_seconds),
                    )
                    .distinct(FeatureWindow.host_id)
                    .order_by(FeatureWindow.host_id, FeatureWindow.window_end.desc())
                )
            ).all()
            for window in windows:
                if not eligible(
                    window,
                    self.scorer.metadata,
                    now,
                    self.settings.inference_max_age_seconds,
                ):
                    continue
                existing = await session.scalar(
                    select(Prediction.id).where(
                        Prediction.host_id == window.host_id,
                        Prediction.model_version_id == self.model_id,
                        Prediction.feature_window_end == window.window_end,
                    )
                )
                if existing is not None:
                    continue
                incident = await session.scalar(
                    select(Incident).where(
                        Incident.host_id == window.host_id,
                        Incident.resolved_at.is_(None),
                    )
                )
                started = time.perf_counter()
                try:
                    score = await asyncio.to_thread(self.scorer.score, window.feature_values, False)
                except Exception:
                    self.duration.observe(time.perf_counter() - started)
                    self.errors.inc()
                    logger.error("Feature scoring failed; record skipped")
                    continue
                high = score["failure_probability"] >= self.scorer.metadata["threshold"]
                previous_probability = await session.scalar(
                    select(Prediction.probability)
                    .where(
                        Prediction.host_id == window.host_id,
                        Prediction.model_version_id == self.model_id,
                        Prediction.feature_window_end < window.window_end,
                    )
                    .order_by(Prediction.feature_window_end.desc())
                    .limit(1)
                )
                crossing = previous_probability is None or (
                    previous_probability < self.scorer.metadata["threshold"]
                )
                if high and (incident is None or crossing):
                    try:
                        score = await asyncio.to_thread(self.scorer.score, window.feature_values)
                        self.explanations.inc()
                    except Exception:
                        self.errors.inc()
                        score["shap"] = {
                            "status": "unavailable",
                            "reason": "Explanation failed; score preserved",
                        }
                elif high:
                    assert incident is not None
                    score["shap"] = {
                        "status": "existing_condition",
                        "incident_id": str(incident.id),
                    }
                self.duration.observe(time.perf_counter() - started)
                predicted_at = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(predicted_at, datetime)
                if (
                    predicted_at - window.window_end
                ).total_seconds() > self.settings.inference_max_age_seconds:
                    continue
                valid_until = window.window_end + timedelta(
                    seconds=self.scorer.metadata["horizon_seconds"]
                )
                explanation: dict[str, Any] = {
                    **score,
                    "model_validation_warning": (
                        "Held-out recall/F1 0; poor calibration; not operationally validated"
                    ),
                    "forecast_valid_until": valid_until.isoformat(),
                    "quality": window.quality,
                }
                prediction = Prediction(
                    host_id=window.host_id,
                    model_version_id=self.model_id,
                    predicted_at=predicted_at,
                    feature_window_end=window.window_end,
                    schema_version=window.schema_version,
                    probability=score["failure_probability"],
                    anomaly_score=score["anomaly_score"],
                    horizon_seconds=self.scorer.metadata["horizon_seconds"],
                    explanation=explanation,
                )
                session.add(prediction)
                await session.flush()
                if high and incident is None:
                    host = await session.get(Host, window.host_id)
                    experiments = (
                        await session.scalars(
                            select(ChaosExperiment).where(
                                ChaosExperiment.host_id == window.host_id,
                                ChaosExperiment.started_at <= predicted_at,
                                ChaosExperiment.ended_at.is_(None),
                            )
                        )
                    ).all()
                    previous_alerts = (
                        await session.scalars(
                            select(Alert)
                            .join(Incident)
                            .where(Incident.host_id == window.host_id)
                            .order_by(Alert.created_at.desc())
                            .limit(3)
                        )
                    ).all()
                    facts = [
                        (
                            f"Host {window.host_id}, service {host.service_job if host else None}: "
                            f"model probability {score['failure_probability']:.6f}; "
                            f"horizon {prediction.horizon_seconds}s anchored at "
                            f"{window.window_end.isoformat()}, "
                            f"valid until {valid_until.isoformat()}."
                        ),
                        (
                            f"Separate unfamiliarity score {score['anomaly_score']:.6f}; "
                            "it is not an imminent-failure probability."
                        ),
                        explanation["model_validation_warning"],
                        "Supplied SHAP associations: " + json.dumps(score["top_drivers"]),
                        "Recorded active experiments: "
                        + json.dumps(
                            [
                                {
                                    "id": str(e.id),
                                    "type": e.failure_type,
                                    "status": e.status,
                                }
                                for e in experiments
                            ]
                        ),
                        "Recent alerts: "
                        + json.dumps(
                            [{"id": str(a.id), "status": a.status} for a in previous_alerts]
                        ),
                        "Causal feature changes: "
                        + json.dumps(
                            {
                                key: value
                                for key, value in window.features.items()
                                if key.endswith(".delta") or key.endswith(".linear_trend")
                            }
                        ),
                    ]
                    summary = await summarize(
                        facts, self.provider, self.llm_requests, self.llm_failures
                    )
                    summary["forecast_valid_until"] = valid_until.isoformat()
                    incident = Incident(
                        host_id=window.host_id,
                        model_version_id=self.model_id,
                        prediction_id=prediction.id,
                        predicted_at=predicted_at,
                        status="open",
                        summary=summary,
                    )
                    session.add(incident)
                    await session.flush()
                    payload = {
                        "incident_id": str(incident.id),
                        "host": window.host_id,
                        "service": host.service_job if host else None,
                        "failure_probability": score["failure_probability"],
                        "anomaly_score": score["anomaly_score"],
                        "horizon_seconds": prediction.horizon_seconds,
                        "feature_window_end": window.window_end.isoformat(),
                        "predicted_at": predicted_at.isoformat(),
                        "forecast_valid_until": valid_until.isoformat(),
                        "top_drivers": score["top_drivers"],
                        "summary": summary,
                        "dashboard_link": str(self.settings.dashboard_url),
                        "model_version": self.scorer.metadata["version"],
                        "model_validation_warning": explanation["model_validation_warning"],
                        "timing": "Forecast warning; future degradation is unconfirmed",
                    }
                    for channel in self.destinations:
                        queued_count += 1
                        last = next((a for a in previous_alerts if a.channel == channel), None)
                        next_attempt = (
                            max(
                                predicted_at,
                                last.created_at
                                + timedelta(seconds=self.settings.alert_cooldown_seconds),
                            )
                            if last
                            else predicted_at
                        )
                        session.add(
                            Alert(
                                incident_id=incident.id,
                                channel=channel,
                                status="pending",
                                payload=payload,
                                next_attempt_at=next_attempt,
                            )
                        )
                if high and incident is not None:
                    prediction.explanation = {
                        **explanation,
                        "incident_id": str(incident.id),
                    }
                count += 1
                high_count += int(high)
        if count:
            self.predictions.inc(count)
            self.high_risk.inc(high_count)
            self.alerts.inc(queued_count)
            self.success.set(time.time())
        return count

    async def send_pending(self) -> None:
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            alerts = (
                await session.scalars(
                    select(Alert)
                    .where(
                        Alert.status.in_(["pending", "retry"]),
                        Alert.next_attempt_at <= now,
                        Alert.attempt_count < 3,
                    )
                    .order_by(Alert.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(10)
                )
            ).all()
            for alert in alerts:
                incident = await session.get(Incident, alert.incident_id)
                if incident is not None and incident.failure_event_id is not None:
                    event = await session.get(FailureEvent, incident.failure_event_id)
                    if event is not None:
                        alert.payload = {
                            **alert.payload,
                            "actual_failure_onset": event.observed_at.isoformat(),
                            "timing": "Degradation already observed; no pre-failure alert claim",
                        }
                alert.status = "sending"
                alert.attempt_count += 1
        # Sending is durable before HTTP: interruption is marked unknown on restart.
        for alert in alerts:
            destination = self.destinations.get(alert.channel)
            status, error = (
                await deliver(
                    self.client,
                    destination,
                    alert.channel,
                    str(alert.id),
                    alert.payload,
                )
                if destination
                else ("failed", "Channel not configured")
            )
            async with self.sessions() as session, session.begin():
                stored = await session.get(Alert, alert.id)
                assert stored is not None
                now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(now, datetime)
                if status == "retry" and stored.attempt_count >= 3:
                    status = "failed"
                stored.status, stored.last_error = status, error
                stored.next_attempt_at = (
                    now + timedelta(seconds=30 * 2 ** (stored.attempt_count - 1))
                    if status == "retry"
                    else None
                )
                if status == "delivered":
                    stored.delivered_at = now
                    self.delivered.labels(stored.channel).inc()
            if status != "delivered":
                self.alert_failures.inc()

    async def cycle(self) -> int:
        self.heartbeat.set(time.time())
        await self.correlate()
        count = await self.score_windows()
        await self.correlate()
        await self.send_pending()
        await self.correlate()
        try:
            self.proposal_submissions.inc(await self.proposals.cycle())
        except Exception:
            self.proposal_errors.inc()
            logger.error("Optional proposal intake failed; inference scores preserved")
        if count:
            self.worker_success.set(time.time())
        return count


async def run(once: bool = False) -> None:
    settings = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    if settings.postgres_host == "localhost":
        settings = settings.model_copy(update={"postgres_host": "127.0.0.1"})
    configure_logging(settings.log_level)
    scorer = Scorer(ROOT / settings.inference_model_path)
    engine = create_database_engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    registry = CollectorRegistry()
    Gauge(
        "sentinel_model_loaded",
        "Verified frozen model loaded in this process",
        ["model_version"],
        registry=registry,
    ).labels(scorer.metadata["version"]).set(1)
    server, thread = start_http_server(
        settings.inference_metrics_port,
        addr=settings.inference_metrics_bind_address,
        registry=registry,
    )
    try:
        async with engine.connect() as lock_connection:
            acquired = await lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK}
            )
            await lock_connection.commit()
            if not acquired:
                raise RuntimeError("Another inference worker already holds the database lease")
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                worker = Worker(settings, engine, scorer, client, registry)
                await worker.initialize()
                while not stop.is_set():
                    held = await lock_connection.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                            "AND pid=pg_backend_pid() AND objid=:key AND granted)"
                        ),
                        {"key": LOCK},
                    )
                    await lock_connection.commit()
                    if not held:
                        raise RuntimeError("Inference worker lost its database lease")
                    try:
                        count = await worker.cycle()
                        logger.info(
                            "Inference cycle complete",
                            extra={"component": "native-inference"},
                        )
                        if once:
                            print(
                                json.dumps(
                                    {
                                        "model_version": scorer.metadata["version"],
                                        "new_predictions": count,
                                    }
                                )
                            )
                            break
                    except Exception:
                        worker.errors.inc()
                        logger.error("Inference cycle failed; no fabricated scores")
                        if once:
                            raise
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=settings.inference_poll_seconds)
                    except TimeoutError:
                        pass
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})
            await lock_connection.commit()
    finally:
        await engine.dispose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(parser.parse_args().once), loop_factory=create_event_loop)


if __name__ == "__main__":
    main()
