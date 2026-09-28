# Free hosting replacement for Railway

Checked against provider documentation on 2026-09-27. Railway is no longer the selected deployment target because the user's trial has ended. The existing Railway preparation files are retained as historical work; they are not a deployment instruction for the next rollout. No replacement provider has been provisioned and no paid resources have been authorized.

Sentinel requires more than static hosting: the authenticated API, persistent PostgreSQL/TimescaleDB, existing Python 3.14 workers, telemetry querying and the React console must have a working deployment path. Redis and Celery remain outside the accepted Phase 2 scope. Hosted role provisioning and the tenant adversarial suite must pass before a hosted database is accepted. Keep credentials in private provider variables.

| Candidate | Verified free offering | Fit and unresolved gates |
| --- | --- | --- |
| Oracle Cloud Always Free | A1 ARM allowance equivalent to 2 OCPUs and 12 GB memory; 200 GB total boot/block storage in the home region | Strongest candidate for a complete self-hosted demo. Requires VM administration and ARM validation because the current Linux inference release is validated on amd64. Capacity can be unavailable and idle instances can be reclaimed. Card verification is normally required. |
| Northflank Developer Sandbox | Two always-on services, one database/addon and two jobs | Promising managed pilot. PostgreSQL documents TimescaleDB support. Confirm the exact free resource allocations, extension version and role privileges before freezing a deployment. The entire current multi-service stack cannot be assumed to fit two small services. Card verification is required. |
| Render free web/static hosting | Free static frontend and web service; API sleeps after 15 idle minutes | Suitable for a limited browser/API preview. Background workers are outside the free service types; no persistent disk. Free managed PostgreSQL expires after 30 days, so it is not a durable replacement for Sentinel's database. |

Do not remove TimescaleDB features or claim a fully running prediction pipeline merely to fit a free tier. A preview must clearly report unavailable workers or telemetry. Preserve the existing demo database and keep the validated workers until an equivalent replacement passes migration, RLS, telemetry and inference checks. Provider selection does not authorize a paid upgrade.

## Sources

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle card verification FAQ](https://www.oracle.com/asean/cloud/free/faq/)
- [Northflank Sandbox pricing](https://northflank.com/pricing)
- [Northflank billing and payment verification](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)
- [Northflank PostgreSQL extensions](https://www.northflank.ai/docs/v1/application/databases-and-persistence/deploy-databases-on-northflank/deploy-postgresql-on-northflank)
- [Render free service limits](https://render.com/docs/free)
