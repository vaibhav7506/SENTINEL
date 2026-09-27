"""Bounded webhook delivery; ambiguous outcomes are never blindly retried."""

from typing import Any
from urllib.parse import urlparse

import httpx


def validate_destination(url: str) -> None:
    parsed = urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ValueError("Invalid webhook destination")
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ValueError("Webhooks require HTTPS except for loopback tests")


def slack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Plain-text blocks prevent telemetry strings becoming Slack mentions.
    core = (
        f"Incident {payload['incident_id']} · Host {payload['host']} · "
        f"Service {payload.get('service')}\n"
        f"Failure probability {payload['failure_probability']:.6f}; "
        f"horizon {payload['horizon_seconds']}s; "
        f"valid until {payload['forecast_valid_until']}\n"
        f"Separate anomaly score {payload['anomaly_score']:.6f}\n"
        f"{payload['model_validation_warning']}\n"
        f"Dashboard: {payload['dashboard_link']}"
    )
    drivers = "\n".join(f"{d['feature']}: {d['contribution']:+.6f}" for d in payload["top_drivers"])
    sections = [
        core,
        "Associated signals (not causes):\n" + drivers,
        "Summary excerpt:\n" + payload["summary"]["summary"][:1800],
    ]
    return {
        "text": "Sentinel model warning",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text": section[:2900],
                    "emoji": False,
                },
            }
            for section in sections
        ],
    }


async def deliver(
    client: httpx.AsyncClient,
    url: str,
    channel: str,
    identifier: str,
    payload: dict[str, Any],
) -> tuple[str, str | None]:
    validate_destination(url)
    try:
        response = await client.post(
            url,
            json=slack_payload(payload) if channel == "slack" else payload,
            headers={"Idempotency-Key": identifier},
            timeout=5,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return (
            "unknown",
            "Transport failed; delivery outcome unknown; no automatic retry",
        )
    if 200 <= response.status_code < 300:
        return "delivered", None
    if response.status_code == 429 or response.status_code >= 500:
        return "retry", "Receiver returned a retryable rejection"
    return "failed", "Receiver rejected delivery"
