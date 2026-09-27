"""Provider-selected fact IDs; all displayed prose is rendered from supplied facts."""

import json
from typing import Any, Protocol

import httpx
from prometheus_client import Counter
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings

CHECKS = {
    "telemetry": "Inspect the supplied telemetry and probe history.",
    "service": "Check application latency and error status.",
    "experiment": "Check whether the recorded demo experiment is still active.",
    "model": "Review the model's poor held-out recall and calibration before operational use.",
}


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_ids: list[int] = Field(max_length=20)
    checks: list[str] = Field(max_length=4)


class Provider(Protocol):
    async def select(self, facts: list[str]) -> Selection: ...


class HTTPProvider:
    """Vendor-neutral LLM gateway protocol: select supplied IDs and allowed checks."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def select(self, facts: list[str]) -> Selection:
        if self.settings.llm_base_url is None:
            raise ValueError("LLM gateway not configured")
        headers = {}
        if self.settings.llm_api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + self.settings.llm_api_key.get_secret_value()
        async with self.client.stream(
            "POST",
            str(self.settings.llm_base_url),
            headers=headers,
            json={
                "model": self.settings.llm_model,
                "instruction": (
                    "Select only relevant supplied evidence IDs and allowed check keys. "
                    "Return evidence_ids and checks. Do not generate operational claims."
                ),
                "facts": [{"id": i, "text": fact} for i, fact in enumerate(facts)],
                "allowed_checks": CHECKS,
            },
            timeout=5,
        ) as response:
            response.raise_for_status()
            payload = b""
            async for chunk in response.aiter_bytes():
                payload += chunk
                if len(payload) > 16384:
                    raise ValueError("LLM response too large")
        return Selection.model_validate(json.loads(payload))


async def summarize(
    facts: list[str],
    provider: Provider | None = None,
    requests: Counter | None = None,
    failures: Counter | None = None,
) -> dict[str, Any]:
    if len(facts) < 3:
        raise ValueError("Core model facts are required")
    selection = Selection(
        evidence_ids=list(range(len(facts))), checks=["telemetry", "service", "model"]
    )
    source = "deterministic_fallback"
    if provider is not None:
        if requests is not None:
            requests.inc()
        try:
            proposed = await provider.select(facts)
            if (
                not proposed.evidence_ids
                or any(i < 0 or i >= len(facts) for i in proposed.evidence_ids)
                or any(key not in CHECKS for key in proposed.checks)
            ):
                raise ValueError("Unsupported LLM claims")
            selection = proposed
            source = "llm_selected_supplied_facts"
        except Exception:
            # No vendor response, credentials or arbitrary prose is logged or displayed.
            if failures is not None:
                failures.inc()
    # Core probability, forecast horizon and model warning always remain visible.
    ids = sorted(set(selection.evidence_ids) | {0, 1, 2})
    evidence = [facts[i] for i in ids]
    return {
        "title": "Model threshold crossing requires review",
        "summary": " ".join(evidence),
        "evidence": evidence,
        "recommendedChecks": [CHECKS[key] for key in dict.fromkeys([*selection.checks, "model"])],
        "possiblePattern": (
            "Supplied feature associations may indicate a change in behavior; "
            "they do not establish causation."
        ),
        "confidenceNotes": (
            "The current model missed every positive held-out test window and had poor "
            "held-out calibration. This is an unvalidated model warning, "
            "not a confirmed future outage or root cause."
        ),
        "source": source,
    }
