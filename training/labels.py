"""Causal feature rows, evidence-based future labels, conservative coverage."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Event:
    id: str
    host_id: str
    experiment_id: str | None
    failure_type: str
    onset: datetime
    confirmed: datetime
    recovered: datetime | None


@dataclass(frozen=True)
class Probe:
    at: datetime
    healthy: bool
    criteria: dict[str, Any] | None


@dataclass(frozen=True)
class Label:
    value: int | None
    reason: str
    event_id: str | None = None
    experiment_id: str | None = None


def label_window(
    host_id: str,
    end: datetime,
    horizon: int,
    recovery_exclusion: int,
    events: list[Event],
    probes: list[Probe],
    criteria: dict[str, Any],
    as_of: datetime,
    max_gap: float = 10,
    consecutive_breaches: int = 1,
    confirmation_span: int = 15,
) -> Label:
    if horizon <= 0 or recovery_exclusion < 0 or consecutive_breaches < 1 or confirmation_span < 1:
        raise ValueError("Invalid label durations")
    future_end = end + timedelta(seconds=horizon)
    known = [
        event
        for event in events
        if event.host_id == host_id and event.onset <= event.confirmed <= as_of
    ]
    for event in known:
        if event.onset <= end and (
            event.recovered is None
            or end <= event.recovered + timedelta(seconds=recovery_exclusion)
        ):
            return Label(None, "active_failure_or_recovery")
    upcoming = sorted(
        (event for event in known if end < event.onset <= future_end),
        key=lambda event: event.onset,
    )
    if upcoming:
        event = upcoming[0]
        return Label(1, "confirmed_future_failure", event.id, event.experiment_id)
    if future_end > as_of:
        return Label(None, "right_censored")
    coverage_end = future_end + timedelta(
        seconds=confirmation_span if consecutive_breaches > 1 else 0
    )
    if coverage_end > as_of:
        return Label(None, "right_censored_confirmation")
    ordered = sorted((probe for probe in probes if probe.at <= as_of), key=lambda probe: probe.at)
    times = [probe.at for probe in ordered]
    left = bisect_right(times, end) - 1
    right = bisect_left(times, coverage_end)
    if left < 0 or right >= len(ordered):
        return Label(None, "incomplete_observation_coverage")
    covered = ordered[left : right + 1]
    if (end - covered[0].at).total_seconds() > max_gap or (
        covered[-1].at - coverage_end
    ).total_seconds() > max_gap:
        return Label(None, "incomplete_observation_coverage")
    if any(
        (b.at - a.at).total_seconds() > max_gap for a, b in zip(covered, covered[1:], strict=False)
    ):
        return Label(None, "observation_gap")
    if any(probe.criteria != criteria for probe in covered):
        return Label(None, "unknown_or_changed_criteria")
    streak: list[Probe] = []
    for probe in covered:
        if probe.healthy:
            streak = []
            continue
        streak.append(probe)
        if len(streak) >= consecutive_breaches:
            recent = streak[-consecutive_breaches:]
            if (
                recent[0].at <= future_end
                and (recent[-1].at - recent[0].at).total_seconds() <= confirmation_span
            ):
                return Label(None, "unconfirmed_degradation")
    return Label(
        0,
        "observed_healthy_horizon"
        if consecutive_breaches == 1
        else "observed_no_sustained_failure_horizon",
    )


def grouped_split(
    rows: list[dict[str, Any]],
    experiment_intervals: list[tuple[str, str, datetime, datetime]],
    horizon: int,
    recovery_exclusion: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Merge overlapping experiment influence intervals before assigning splits.
    intervals = sorted(experiment_intervals, key=lambda item: (item[1], item[2], item[0]))
    groups: list[tuple[str, datetime, datetime, set[str]]] = []
    for identifier, host, start, end in intervals:
        start -= timedelta(seconds=horizon)
        end += timedelta(seconds=recovery_exclusion)
        if groups and groups[-1][0] == host and start <= groups[-1][2]:
            previous = groups[-1]
            groups[-1] = (
                host,
                previous[1],
                max(end, previous[2]),
                previous[3] | {identifier},
            )
        else:
            groups.append((host, start, end, {identifier}))
    for row in rows:
        start, end = (
            datetime.fromisoformat(row["window_start"]),
            datetime.fromisoformat(row["window_end"]),
        )
        related_ids: set[str] = set()
        for host, influence_start, influence_end, identifiers in groups:
            if (
                row["host_id"] == host
                and start <= influence_end
                and end + timedelta(seconds=horizon) >= influence_start
            ):
                related_ids |= identifiers
        # Windows that connect experiment groups join those groups.
        row["experiment_ids"] = sorted(related_ids)
    parents: dict[str, str] = {
        identifier: identifier for row in rows for identifier in row["experiment_ids"]
    }

    def root(identifier: str) -> str:
        while parents[identifier] != identifier:
            identifier = parents[identifier]
        return identifier

    for row in rows:
        ids = row["experiment_ids"]
        for identifier in ids[1:]:
            parents[root(identifier)] = root(ids[0])
    for row in rows:
        ids = row["experiment_ids"]
        row["group_id"] = (
            "experiment:" + root(ids[0])
            if ids
            else "healthy:" + row["host_id"] + ":" + row["window_end"][:10]
        )
    ordered_groups = sorted(
        {row["group_id"] for row in rows},
        key=lambda group: (
            min(row["window_end"] for row in rows if row["group_id"] == group),
            group,
        ),
    )
    test_groups = (
        set(ordered_groups[-max(1, len(ordered_groups) // 5) :])
        if len(ordered_groups) >= 2
        else set()
    )
    test = [row for row in rows if row["group_id"] in test_groups]
    purged = 0
    for row in rows:
        row["split"] = "test" if row["group_id"] in test_groups else "train"
        if row["split"] == "train":
            a, b = (
                datetime.fromisoformat(row["window_start"]),
                datetime.fromisoformat(row["window_end"]) + timedelta(seconds=horizon),
            )
            if any(
                other["host_id"] == row["host_id"]
                and a <= datetime.fromisoformat(other["window_end"]) + timedelta(seconds=horizon)
                and b >= datetime.fromisoformat(other["window_start"])
                for other in test
            ):
                row["split"] = "purged"
                purged += 1
    assigned = [row for row in rows if row["split"] != "purged"]
    return assigned, {
        "method": "chronological experiment groups with interval purge",
        "group_count": len(ordered_groups),
        "purged_samples": purged,
        "status": "available"
        if test and any(row["split"] == "train" for row in assigned)
        else "insufficient_independent_groups",
    }


def chronological_split(
    rows: list[dict[str, Any]],
    experiments: list[tuple[str, str, datetime, datetime]],
    horizon: int,
    plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep complete input and future intervals within predeclared partitions."""
    train_end = datetime.fromisoformat(plan["train_boundary"])
    test_start = datetime.fromisoformat(plan["test_boundary"])
    declared = datetime.fromisoformat(plan["declared_at"])
    if not train_end < test_start or not declared < test_start:
        raise ValueError("Boundaries must be predeclared before the held-out period")
    assigned = []
    purged = 0
    owners: dict[str, str] = {}
    for original in rows:
        row = dict(original)
        start = datetime.fromisoformat(row["window_start"])
        end = datetime.fromisoformat(row["window_end"]) + timedelta(seconds=horizon)
        partition = (
            "train"
            if end < train_end
            else "validation"
            if start > train_end and end < test_start
            else "test"
            if start > test_start
            else None
        )
        if partition is None:
            purged += 1
            continue
        related = sorted(
            identifier
            for identifier, host, a, b in experiments
            if host == row["host_id"] and start <= b and end >= a
        )
        for identifier in related:
            if identifier in owners and owners[identifier] != partition:
                raise ValueError("One experiment cannot occur in multiple partitions")
            owners[identifier] = partition
        row.update(
            split=partition,
            experiment_ids=related,
            group_id="chronological:" + partition,
        )
        assigned.append(row)
    return assigned, {
        **plan,
        "method": "predeclared chronological partitions with interval purge",
        "purged_samples": purged,
        "status": "available"
        if {r["split"] for r in assigned} == {"train", "validation", "test"}
        else "awaiting_held_out_evidence",
        "strict_boundary": "window_start > lower; window_end + purge horizon < upper",
        "purge_horizon_seconds": horizon,
    }
