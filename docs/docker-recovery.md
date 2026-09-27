# Local Docker runtime recovery

On 2026-09-26, Docker Desktop 4.83.0 failed before its Linux engine started. The backend log reported Windows error 1920 while replacing `dockerInference`, then `engine.sock`. The API, PostgreSQL, and Prometheus were unavailable because the engine had not started.

Both affected entries were zero-content runtime sockets with the `ReparsePoint` attribute. Individual rename and removal failed. This matches the socket failure described in [Docker's issue tracker](https://github.com/docker/desktop-feedback/issues/460).

Recovery preserved and recreated these two directories together while Docker was stopped:

- `C:/Users/vs250/AppData/Local/Docker/run`
- `C:/Users/vs250/AppData/Local/docker-secrets-engine`

Before moving either directory, its absolute path and ordinary-directory attributes were verified, and its entries were checked against the expected socket names. Each original directory remains beside its replacement with a `.sentinel-backup-<timestamp>` suffix. The secrets-engine directory contained only `engine.sock`; no stored secrets were moved. Container images, database volumes, Prometheus history, Docker settings, and credentials were preserved.

Recreating only one directory was insufficient: a failed startup left a fresh inaccessible socket behind in that directory while exposing the other stale socket. Recreating both directories before the next start allowed Docker Engine 29.6.2 to start.

The existing nine Sentinel containers resumed. `scripts/verify_stack.py` passed all seven HTTP checks and all expected container health checks. The native inference worker and local receiver were restarted separately. The worker correctly rejects insufficient post-outage history while telemetry accumulates; it does not turn a monitoring gap into a fresh forecast.

This was a repair of stopped runtime directories, not a recurring startup procedure. A healthy running Docker instance should retain its runtime directories. Use Docker's normal shutdown controls when stopping it; forced termination can leave these sockets behind.

Current acceptance checks are recorded in [Phase 6 validation](phase-6-validation.json). After restoring the stack, verify fresh inference without injecting additional faults:

```powershell
uv run --project inference python scripts/verify_inference_state.py
```

Exit status 2 means the API has no recent eligible prediction yet. Wait for actual feature history, then rerun. Successful verification records `docs/phase-6-restored-live.json` and checks frozen model score reproduction, causal feature coverage, forecast validity, bounded APIs, database uniqueness, inference metrics, Prometheus scraping, and deduplication of existing local webhook receipts after receiver restart.
