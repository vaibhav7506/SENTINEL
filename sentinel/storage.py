import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class HealthCheck:
    id: int | None
    service_name: str
    timestamp: datetime
    status_code: int | None
    latency_ms: float | None
    success: bool


@dataclass
class Incident:
    id: int | None
    service_name: str
    started_at: datetime
    resolved_at: datetime | None
    failure_type: str
    remediation_action: str | None
    remediation_success: bool | None


@dataclass
class DriftEvent:
    id: int | None
    service_name: str
    timestamp: datetime
    diff_json: str


@dataclass
class Prediction:
    id: int | None
    service_name: str
    timestamp: datetime
    failure_probability: float
    model_version: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status_code INTEGER,
    latency_ms REAL,
    success BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    resolved_at TEXT,
    failure_type TEXT NOT NULL,
    remediation_action TEXT,
    remediation_success BOOLEAN
);

CREATE TABLE IF NOT EXISTS drift_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    diff_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    failure_probability REAL NOT NULL,
    model_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_checks_service_time ON health_checks(service_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_incidents_service_time ON incidents(service_name, started_at);
CREATE INDEX IF NOT EXISTS idx_drift_events_service_time ON drift_events(service_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_predictions_service_time ON predictions(service_name, timestamp);
"""


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def transaction(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def insert_health_check(self, check: HealthCheck) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO health_checks (service_name, timestamp, status_code, latency_ms, success)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    check.service_name,
                    check.timestamp.isoformat(),
                    check.status_code,
                    check.latency_ms,
                    check.success,
                ),
            )
            return cursor.lastrowid

    def get_recent_health_checks(
        self, service_name: str, limit: int = 100
    ) -> list[HealthCheck]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, service_name, timestamp, status_code, latency_ms, success
                FROM health_checks
                WHERE service_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (service_name, limit),
            ).fetchall()

        return [
            HealthCheck(
                id=row["id"],
                service_name=row["service_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                status_code=row["status_code"],
                latency_ms=row["latency_ms"],
                success=bool(row["success"]),
            )
            for row in reversed(rows)
        ]

    def get_health_checks_since(
        self, service_name: str, since: datetime
    ) -> list[HealthCheck]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, service_name, timestamp, status_code, latency_ms, success
                FROM health_checks
                WHERE service_name = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (service_name, since.isoformat()),
            ).fetchall()

        return [
            HealthCheck(
                id=row["id"],
                service_name=row["service_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                status_code=row["status_code"],
                latency_ms=row["latency_ms"],
                success=bool(row["success"]),
            )
            for row in rows
        ]

    def insert_incident(self, incident: Incident) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO incidents (service_name, started_at, resolved_at, failure_type, remediation_action, remediation_success)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.service_name,
                    incident.started_at.isoformat(),
                    incident.resolved_at.isoformat() if incident.resolved_at else None,
                    incident.failure_type,
                    incident.remediation_action,
                    incident.remediation_success,
                ),
            )
            return cursor.lastrowid

    def update_incident_resolution(
        self, incident_id: int, resolved_at: datetime, remediation_action: str, success: bool
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE incidents
                SET resolved_at = ?, remediation_action = ?, remediation_success = ?
                WHERE id = ?
                """,
                (resolved_at.isoformat(), remediation_action, success, incident_id),
            )

    def get_open_incident(self, service_name: str) -> Incident | None:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, service_name, started_at, resolved_at, failure_type, remediation_action, remediation_success
                FROM incidents
                WHERE service_name = ? AND resolved_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (service_name,),
            ).fetchone()

        if row is None:
            return None

        return Incident(
            id=row["id"],
            service_name=row["service_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            failure_type=row["failure_type"],
            remediation_action=row["remediation_action"],
            remediation_success=bool(row["remediation_success"]) if row["remediation_success"] is not None else None,
        )

    def get_incidents(
        self, service_name: str | None = None, since: datetime | None = None, limit: int = 100
    ) -> list[Incident]:
        query = "SELECT id, service_name, started_at, resolved_at, failure_type, remediation_action, remediation_success FROM incidents WHERE 1=1"
        params: list[Any] = []

        if service_name:
            query += " AND service_name = ?"
            params.append(service_name)
        if since:
            query += " AND started_at >= ?"
            params.append(since.isoformat())

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            Incident(
                id=row["id"],
                service_name=row["service_name"],
                started_at=datetime.fromisoformat(row["started_at"]),
                resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
                failure_type=row["failure_type"],
                remediation_action=row["remediation_action"],
                remediation_success=bool(row["remediation_success"]) if row["remediation_success"] is not None else None,
            )
            for row in rows
        ]

    def insert_drift_event(self, event: DriftEvent) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO drift_events (service_name, timestamp, diff_json)
                VALUES (?, ?, ?)
                """,
                (event.service_name, event.timestamp.isoformat(), event.diff_json),
            )
            return cursor.lastrowid

    def get_drift_events(
        self, service_name: str | None = None, since: datetime | None = None, limit: int = 100
    ) -> list[DriftEvent]:
        query = "SELECT id, service_name, timestamp, diff_json FROM drift_events WHERE 1=1"
        params: list[Any] = []

        if service_name:
            query += " AND service_name = ?"
            params.append(service_name)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            DriftEvent(
                id=row["id"],
                service_name=row["service_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                diff_json=row["diff_json"],
            )
            for row in rows
        ]

    def insert_prediction(self, prediction: Prediction) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO predictions (service_name, timestamp, failure_probability, model_version)
                VALUES (?, ?, ?, ?)
                """,
                (
                    prediction.service_name,
                    prediction.timestamp.isoformat(),
                    prediction.failure_probability,
                    prediction.model_version,
                ),
            )
            return cursor.lastrowid

    def get_latest_prediction(self, service_name: str) -> Prediction | None:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, service_name, timestamp, failure_probability, model_version
                FROM predictions
                WHERE service_name = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (service_name,),
            ).fetchone()

        if row is None:
            return None

        return Prediction(
            id=row["id"],
            service_name=row["service_name"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            failure_probability=row["failure_probability"],
            model_version=row["model_version"],
        )

    def get_predictions_since(
        self, service_name: str, since: datetime
    ) -> list[Prediction]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, service_name, timestamp, failure_probability, model_version
                FROM predictions
                WHERE service_name = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (service_name, since.isoformat()),
            ).fetchall()

        return [
            Prediction(
                id=row["id"],
                service_name=row["service_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                failure_probability=row["failure_probability"],
                model_version=row["model_version"],
            )
            for row in rows
        ]

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None