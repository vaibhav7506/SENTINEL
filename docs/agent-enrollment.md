# Agent enrollment and verification

An OWNER or ADMIN opens Hosts → Add host and generates an expiring, single-use enrollment command. Treat the temporary token as private. The server chooses account and host identity; the agent cannot select either.

For Linux, use the generated HTTPS installer. It downloads the served deterministic, credential-free bundle, verifies its digest and writes `/etc/sentinel/agent.toml` with owner-only permissions. The installer/service require the user's authorized host administration. The package endpoint contains no credentials. The protected configuration contains the permanent key; do not upload it or paste it into chat.

For local Windows development, use the checked-in agent environment and a private path inside `.runtime`:

```powershell
# The generated command supplies your real server and one-time token privately.
agent/.venv/Scripts/python.exe -m sentinel_agent.identity --server http://127.0.0.1:8000 --enrollment-token YOUR_PRIVATE_ONE_TIME_TOKEN --identity-path .runtime/my-agent/agent.toml
$env:AGENT_IDENTITY_PATH = (Resolve-Path .runtime/my-agent/agent.toml).Path
$env:AGENT_DISK_PATH = 'C:\'
agent/.venv/Scripts/python.exe -m sentinel_agent.main
```

Windows identity creation applies restrictive NTFS ACLs; Linux loading verifies owner and mode 0600. Enrollment reuses an existing protected configuration on restart, including when the original token has already been consumed. No permanent token is printed. Keep shell history private when using a generated enrollment command.

The agent samples real psutil metrics every 5 seconds and sends batches every 30 seconds by default. `AGENT_SAMPLE_SECONDS` and `AGENT_REPORTING_SECONDS` configure those intervals. The latter must be 5–300 seconds. The buffer retains at most 120 samples, respects Retry-After, backs off on temporary failures and drops samples older than the ingestion bound. The API's Online/Stale/Offline state follows the declared expected reporting interval. Sampling failures omit current values; they do not invent healthy zero readings.

Restart the agent and confirm the same host and credential remain, new raw metric timestamps arrive and the host is Online. Stop it and observe Stale then Offline. Revoke the permanent credential in Hosts; subsequent ingestion returns 401, while a separate credential remains usable. Revocation does not reenroll automatically.

`scripts/check_real_agent.py` performs a real loopback Windows psutil lifecycle test with its own private fixture identity and writes only sanitized evidence. It never contacts a cloud server. An empty host needs roughly 15 minutes of real feature coverage before a prediction is eligible. Hosted installation, remote restart and cloud predictions must be verified later against the actual HTTPS endpoint.
