# Phase 2: account isolation and agent enrollment

Phase 2 adds a shared-database SaaS foundation to the verified Sentinel baseline. It keeps the validated Python 3.14 feature, observer and inference workers. It does not introduce distributed scoring, Celery, billing or subscription plans. Model artifacts, feature schema, threshold, datasets, historical evidence and ML dependency locks remain unchanged.

## Account and access model

An account owns users, hosts, raw metrics, feature windows, predictions, incidents, alerts, experiments, observations, failure events, remediation proposals, channels, credentials, enrollment tokens, sessions and audit events. A user belongs to one account. Email is normalized by trimming and case folding, and is globally unique for V1. Frozen model versions and held-out evaluation records remain a shared, read-only model catalog.

| Role | Access |
| --- | --- |
| OWNER | Operational reads; team creation and roles; account settings; enrollment and credential revocation; alert integrations; permitted demo experiments; human proposal review |
| ADMIN | Operational reads; enrollment and credential revocation; permitted demo experiments; human proposal review |
| MEMBER | Operational reads; no enrollment, credentials, team management, integration management or destructive experiments |

Every management endpoint enforces roles on the server. The frontend hides unavailable actions for convenience. Role changes take effect on the next authenticated request, including existing sessions. An account must retain an active owner.

Passwords use salted stdlib scrypt with N=131072, r=8, p=1 and a 64-byte output. This follows the memory-hard configuration documented in the [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). No plaintext password is persisted. Browser session and CSRF secrets are random, and only their SHA-256 hashes are stored.

`POST /auth/register`, `POST /auth/login`, `POST /auth/logout` and `GET /auth/me` implement browser sessions. Session cookies are HttpOnly and SameSite=Lax; production uses Secure, Path=/ and the __Host- prefix without a Domain attribute. A separate SameSite=Strict CSRF cookie supplies the header required for authenticated mutations. Production requires an HTTPS public API URL and explicit HTTPS browser origins. Login and registration require JSON and apply a bounded per-client rate limit. Cookies and session data never use localStorage. Logout or HTTP 401 unmounts account views and clears their cached data.

## Two layers of tenant isolation

Browser-facing code uses `request_session` and scoped primary-key lookup. ORM loader criteria restrict tenant tables and account identities. Each transaction also sets LOCAL ROLE sentinel_app and a transaction-local account context. New transactions reapply both; connection pool reuse cannot retain a prior account context.

The migration creates sentinel_app as a NOLOGIN, NOSUPERUSER, NOBYPASSRLS role which does not own the tables. RLS policies use both USING and WITH CHECK. Missing account context exposes no tenant rows. Tests use actual PostgreSQL queries under this role, without ORM filtering, to demonstrate cross-account read and update denial. Both metrics and feature_windows hypertables support and pass these checks on TimescaleDB 2.30.1/PostgreSQL 17.

The trusted migration/table owner remains privileged for credential verification, account creation and the existing single-process workers. PostgreSQL owners ordinarily bypass RLS, as documented in the [PostgreSQL RLS reference](https://www.postgresql.org/docs/17/ddl-rowsecurity.html). Policies are deliberately not FORCE RLS: no owner session is presented as proof of tenant isolation. Browser transactions always switch to the tested non-owner role. Database credentials must remain private; this design is defense against missed query scoping, not against an attacker who controls the entire application server or its owner credential. The database administrator must be able to create and grant the role when provisioning a hosted database.

Ownership triggers derive downstream account IDs from the owning host or incident and reject mismatched secondary references. Host ownership cannot be reassigned. Agents cannot choose account_id or host_id; strict request schemas reject those fields. Cross-account lookups return 404, and role violations return 403. Integration URLs, key hashes, transport payloads and proposal parameters are excluded from browser lists and details.

Audit events are append-only. Browser roles have no UPDATE or DELETE grant; a database trigger also rejects modification by the owner. User creation, role changes, enrollment, credential revocation, integration changes, experiments and proposal creation/approval create audit events. Reads do not create audit noise. Proposal approval records human review only; it neither requests nor claims execution by RunbookOS.

## Host enrollment and installation

An OWNER or ADMIN generates a token through `POST /hosts/enrollment-tokens`. The default lifetime is 600 seconds. The database stores its hash and a short prefix. The response displays the raw token and install command once; token lists never recover it. Tokens can be revoked. The permanent host credential is never embedded in the install command.

`POST /agent/enroll` locks and consumes the token in the same transaction as creation of the server-assigned host identity and its independent credential. Concurrent attempts produce one success and one rejection. Expired, revoked and consumed tokens fail. The host key is returned once and only its hash and prefix are stored. Revoking one host credential leaves other hosts working.

The Add Host screen serves a fixed source bundle with a checked SHA-256 digest. The Linux installer uses pinned uv and the frozen agent dependency lock, creates an unprivileged systemd service, and enrolls once. Its identity is saved at `/etc/sentinel/agent.toml`, owned by the service user with mode 0600 inside a mode 0700 directory. Restart loads that identity instead of re-enrolling. Existing files are never overwritten, symlinks are rejected and HTTP redirects are refused. HTTPS is required except for development loopback. Windows identities use an ACL restricted to the current user and SYSTEM.

`POST /agent/metrics` verifies the host-specific credential and assigns both host and account from that credential. It accepts bounded, finite known metrics with a recent timezone-aware timestamp. Raw observations persist in a Timescale hypertable. Enrolled host details read this account-scoped history. Existing demo exporter telemetry and the validated prediction pipeline continue through their established Prometheus path. Building distributed workers or a new scoring pipeline for enrolled remote hosts belongs to the next phase and is intentionally absent here; new hosts show unknown risk until eligible predictions exist.

Alert integrations are scoped to their account. Operator approval of generic destination hostnames is required through SAAS_ALERT_ALLOWED_HOSTS; official Slack hooks are allowed by default. Development loopback receivers support local delivery tests. The old global webhook settings are restricted to the migrated demo account. Channel URLs are never shown after saving. Shared operator Grafana dashboards are not tenant browser views and are not linked from the account UI.

## Existing demo data and the first owner

Migration 0007_saas_foundation assigns existing single-user rows to `00000000-0000-4000-8000-000000000001`, named “Sentinel development/demo account.” It does not delete data or manufacture predictions. A backup of actual demo data was restored into a separate database using Timescale's required [pre-restore and post-restore hooks](https://docs.tigerdata.com/api/latest/administration). Before/after checks compare row counts and hashes of every original column, not only totals.

There is no built-in demo password. To claim the migrated demo account locally, run this command and enter your chosen password privately at the prompts:

```sh
docker compose exec sentinel-api python -m app.saas.bootstrap --email YOUR_EMAIL
```

Bootstrap refuses an account that already has a user. Future users are created by its owner through Team & account. Signup creates a separate account and cannot claim the migrated demo account.

## Validation and deployment limits

See [the dated Phase 2 verification record](phase2-verification.md) for acceptance results, 243 unique test contracts, original demo preservation counts and the publication boundary. The original local stack now runs this migration and authenticated frontend.

The adversarial suite creates accounts A and B with actual users, hosts, metrics, feature windows, predictions, incidents and alerts. It attacks host/detail/list routes, metrics, features, predictions, incidents, alerts, experiments, credentials, channels, proposals and team writes. It separately exercises raw RLS queries, pool reuse, missing account context, cross-account updates, enrollment replay/expiry/revocation/races, CSRF, RBAC, key hashing and append-only audit. Test-only scores are confined to disposable databases; they are not product demonstrations or ML evidence.

`scripts/check_phase10_database.py` now runs five disposable database cohorts, including `backend/tests/test_saas_database.py`. Each performs schema comparison and head/base/head migration checks. The Linux CI job invokes this runner, while ordinary unit jobs explicitly exclude database-only tests. The agent source bundle is checked for staleness in CI. Actual local verification logs are retained under ignored `.runtime/phase2`; private passwords, credentials and database backups are not publication artifacts.

All previous inference contracts remain green. The baseline's poor held-out predictive performance remains a material limitation; tenancy changes do not improve or validate its forecasting usefulness. Remote CI, Railway deployment, hosted RLS provisioning, external telemetry and real Slack delivery are not claimed by local checks. The staged public baseline import uses its original frozen manifest; these Phase 2 changes are not silently added to that publisher. Stop after Phase 2.
