"""Loopback-only real HTTP receipt log; never sends messages externally."""

import json
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".runtime" / "local-webhook-receipts.jsonl"
OUTPUT.parent.mkdir(exist_ok=True)
application = FastAPI(title="Sentinel local alert test receiver")
seen: set[str] = set()
for receipt_path in (ROOT / "docs" / "phase-6-webhook-receipts.jsonl", OUTPUT):
    if receipt_path.exists():
        seen.update(
            json.loads(line)["idempotency_key"] for line in receipt_path.read_text().splitlines()
        )


@application.post("/{channel}")
async def receive(channel: str, request: Request) -> dict[str, object]:
    if channel not in {"generic", "slack"}:
        raise HTTPException(404)
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key) > 128:
        raise HTTPException(400, "Idempotency key required")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413)
    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(400, "JSON required") from error
    if key in seen:
        return {"accepted": True, "duplicate_suppressed": True}
    record = {
        "received_at": datetime.now(UTC).isoformat(),
        "channel": channel,
        "idempotency_key": key,
        "kind": request.headers.get("X-Sentinel-Verification", "live_model_warning"),
        "payload": payload,
    }
    with OUTPUT.open("a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(record, allow_nan=False) + "\n")
        target.flush()
    seen.add(key)
    return {"accepted": True, "duplicate_suppressed": False}


if __name__ == "__main__":
    uvicorn.run(application, host="127.0.0.1", port=8091, access_log=False)
