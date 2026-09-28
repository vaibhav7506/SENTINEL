# Proposed SaaS Phase 2 publication

Prepared from the locally verified SaaS foundation on 2026-09-27. This is a proposed follow-up to the completed 32-commit frozen baseline import, not a change to its manifest or journal. Publication has not started. The earlier import's historical “Phase 2” was telemetry; these commits will explicitly identify the new SaaS foundation to avoid confusing the two sequences.

The comparison found 69 reviewable additions/modifications against baseline commit `4af3190d6fa7e306d6044ff7ee95148ccded8e6e`: 24 additions and 45 modifications. This plan adds one documentation file. Private environments, database backups, fixture credentials, runtime evidence, caches and sibling projects are excluded. The selected changes include baseline fixes needed by the verified foundation; frozen model artifacts, historical evidence and native ML locks remain unchanged.

| Batch | Proposed commit title | Scope |
| --- | --- | --- |
| 1/5 | SaaS foundation: add account authentication and tenant isolation | Account/user/session models, migration, tenant repository and RLS context, password/session security, RBAC, audited operations, enrollment endpoints and scoped existing API/worker behavior |
| 2/5 | SaaS foundation: persist agent identity and upload authenticated metrics | Agent enrollment and protected local identity, restart reuse, collection/upload path and deterministic bundle builder |
| 3/5 | SaaS foundation: add account and host management console | Signup/login/logout, account/role context, team and integrations, Hosts/Add Host, private browser session handling and development proxy |
| 4/5 | SaaS foundation: configure secure service and deployment settings | Compose settings, alert destination allowlist, cookie-based Helm proxy, chart schema, self-metrics configuration and inference test image support |
| 5/5 | SaaS foundation: verify isolation and document acceptance | Backend/agent/frontend adversarial and regression contracts, CI database cohorts, bundle/chart checks, baseline audit, Phase 2 acceptance record and updated README |

These are publication batches of completed local work. Some intermediate snapshots depend on later batches; the final batch is the acceptance checkpoint. Each commit should describe its actual final file scope, use its real creation time and be pushed alone. At least 300 seconds must pass between commit creation times, without catch-up bursts or empty padding commits.

Before publishing, freeze this exact selected source set, screen it for configured credentials, confirm the intended remote and its current head, and require a clean isolated publication checkout. Preserve remote changes and refuse an unexpected remote head rather than overwriting it. Use a separate follow-up manifest/journal; the completed baseline publisher must remain unchanged. The final source snapshot must include the validated migration, account-scoped code and its CI coverage together.

Local verification has passed 243 unique contracts and preserves every original demo database value. Hosted deployment and remote CI remain unverified. Publishing this follow-up would not complete Railway deployment acceptance or establish useful forecasting accuracy. See [Phase 2 verification](phase2-verification.md).
