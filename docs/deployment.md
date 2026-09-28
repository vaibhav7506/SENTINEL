# Current SaaS deployment: Oracle + Tiger Cloud + Grafana Cloud

No cloud deployment is verified. The user requested local verification and deployment files until the VM, domain and managed accounts are ready. See [Phase 4 preparation and pending acceptance](phase4-deployment-preparation.md), [security](security.md), [tenancy](tenancy.md) and [agent enrollment](agent-enrollment.md).

Choose the user-selected Oracle Always Free Ampere A1 ARM64 VM, configure DNS to it, allow inbound 80/443 and restrict SSH to operator access. Native ARM CI builds and frozen model checks must pass before rollout; a Compose configuration check alone is insufficient. Use a clean reviewed repository checkout. Generate `.env.oracle` with `python scripts/init_oracle_env.py --hostname YOUR_DOMAIN`; it generates Redis and Fernet keys without printing them and refuses overwrite. Privately fill `DATABASE_URL` from Tiger Cloud with SSL, immutable ARM64 application image references from a successful CI publication, and a reviewed Alloy image digest. Do not paste private environment values into deployment logs (`docker compose config` without `--quiet` can reveal them).

Select Tiger Cloud near the VM. Allow only required secure application connectivity. Before any hosted migration, verify Timescale extension access and creation/granting of the restricted `sentinel_app` NOLOGIN/NOSUPERUSER/NOBYPASSRLS role. Current bootstrap/auth/scheduler use the trusted connection owner; customer transactions switch to that restricted role. If managed permissions cannot meet this contract, stop the rollout and resolve it; do not disable RLS. Tiger's [managed connection guide](https://www.tigerdata.com/docs/learn/tutorials/create-services-with-terraform) supplies SSL connection-string examples. Prefer certificate verification and preserve the generated integration key with encrypted backups.

Do not merge the root development Compose into the production profile. Run the helper from a private operator session:

```sh
# Pull/build reviewed images first. Confirm a real backup and a tested restore privately.
infra/oracle/sentinel.sh config --quiet
infra/oracle/sentinel.sh --profile ops run --rm -e SENTINEL_BACKUP_CONFIRMED=true migrate
infra/oracle/sentinel.sh --profile ops run --rm register
infra/oracle/sentinel.sh up -d --wait
# Once Grafana Cloud environment values are present:
infra/oracle/sentinel.sh --profile observability up -d alloy
```

A failed migration blocks rollout. Keep one Beat scheduler and use the provided Celery command/module; ML runs in a separate locked subprocess. Avoid automatic app-start migrations in production. Revision 0008 is forward-only: rollback restores the verified database backup and matching old application image. Check `/api/health` and `/api/ready` over actual HTTPS, then signup/login/logout, Secure cookies, tenant boundaries, enrollment, raw telemetry, prediction ownership, restart and revocation. Public docs are intentionally blocked. Optional providers never gate liveness.

Import `infra/oracle/platform-dashboard.json` into Grafana Cloud. Alloy uses the official [scrape/remote-write pipeline](https://grafana.com/docs/grafana-cloud/send-data/alloy/tutorials/send-metrics-to-prometheus/) with an additional fixed platform allowlist. Scope the Cloud token to metrics write; never scrape customer agents into that shared observability account. Check metrics arriving after startup and exercise failure counters.

The local profile continues to use the root Compose plus `compose.push.yaml`. Build the sanitized queue context with `python scripts/prepare_push_build.py`, then `docker compose -p sentinel -f compose.yaml -f compose.push.yaml build`. Run the guarded `scripts/upgrade_push_local.py` once for an existing 0007 local database, then bring up Redis, API, frontend, push worker and Beat. A fresh local database may use the root automatic development migration. The original demo worker remains scoped to the development/demo account.

The release material below documents the historical pre-SaaS Kubernetes baseline. Its earlier acceptance claims and head/base/head commands must not be used as current hosted SaaS acceptance. Current migration checks prove forward-only refusal rather than discarding tenant data.

---

# Release and deployment

Phase 10 release preparation is local. **No GitHub CI run, GHCR publication, Kubernetes deployment, Argo CD sync or public HTTPS endpoint has been verified.** The checkout has no Git remote and kubectl has no configured context. These are required acceptance items, not optional claims inferred from manifests.

## Release inputs

Use this Sentinel directory as the repository root; the current parent Git repository also contains unrelated projects and has not been modified. Supply an approved GitHub repository/GHCR namespace, reviewed commit, Kubernetes context/namespace, storage provisioner, ingress controller, DNS hostname and trusted TLS Secret. The chart targets Kubernetes 1.35–1.37; local schema checks use 1.37.0. Supported release images are Linux amd64. Check the [current Kubernetes support policy](https://kubernetes.io/releases/) before a later deployment.

The [workflow](../.github/workflows/ci.yml) runs Windows Python contracts/reproducibility, frontend checks/build, Helm safety and Kubernetes schemas, Linux frozen inference/database tests, and six container builds/scans. Every validation job must pass before the main-branch push job can publish. Critical findings, including unfixed ones, block publication. PRs never publish; no pull-request-target workflow executes contributor code with publishing credentials. Actions, Helm, kubeconform and container bases are pinned to verified versions/digests. See [GitHub's publishing workflow contract](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

Six images are produced: `sentinel-api`, `sentinel-worker`, `sentinel-agent`, `sentinel-demo`, `sentinel-frontend`, and `sentinel-inference`. Observer uses the API image with its own command. Publication uses `ghcr.io/<owner>/sentinel-<component>:sha-<commit>` and exports immutable digest references as workflow artifacts. Production values must specify every used application image digest. SBOM and build provenance are attached during publication; this preparation does not claim either attestation was published or verified.

The independent `inference/linux/uv.lock` uses the official CPU PyTorch wheel for CPython 3.14/Linux x86_64. Windows inference/training locks and frozen artifacts remain unchanged. Historical absolute dataset references resolve by their artifact identity under the current trusted artifact root, then pass the same content hashes/schema checks. Model registration imports the original historical evaluation idempotently; it never trains or invents current forecasts. Joblib remains a trusted-publisher boundary.

Historical metadata source hashes identify the original training source. Current portability, missing-Git handling and offline report isolation code has changed; those historical source hashes are not asserted to match today's source. Two independent fits still match within each platform, and the frozen deployment weights/cutoff remain unchanged.

## Local release checks

Install release tooling in an isolated environment:

```sh
python -m venv .runtime/deploy-tools
# Windows: .runtime/deploy-tools/Scripts/python.exe
# Linux/macOS: .runtime/deploy-tools/bin/python
python -m pip install -r infra/validation/requirements.txt
python scripts/verify_release_chart.py --helm helm --render-dir .runtime/chart-renders
kubeconform -strict -summary -kubernetes-version 1.37.0 .runtime/chart-renders
```

Run the last two Python commands using that environment's interpreter. Helm 4.3.0 and kubeconform 0.8.0 were verified locally. The workflow downloads them from official release URLs and verifies fixed SHA-256 checksums. The render checker covers demo, external-database production, minimal configuration, duplicate YAML keys, workload privileges/probes/resources, RBAC, dashboard datasource consistency and rejection of unsafe inputs.

```sh
docker build --target runtime -t sentinel-api:local backend
docker build --target worker -t sentinel-worker:local backend
docker build --target runtime -t sentinel-agent:local agent
docker build --target runtime -t sentinel-demo:local demo-service
docker build -t sentinel-frontend:local frontend
docker build --target runtime -f inference/Dockerfile -t sentinel-inference:local .
docker build --target test -f inference/Dockerfile -t sentinel-inference:test .
docker run --rm sentinel-inference:test
```

For Windows workspaces with unreadable historical temporary directories, `python scripts/stage_inference_context.py` prepares an explicit source/artifact allowlist. Use `.runtime/phase10/build-context` as the context and its `inference/Dockerfile` as the Dockerfile. Credentials, caches, logs and sibling projects are excluded. The production inference image has neither training environments nor test/dev dependencies; training source is present because frozen scoring shares its transforms and dataset validators.

`scripts/check_phase10_database.py` creates uniquely named disposable database cohorts, runs eleven persistence tests and head/base/head migration checks, then drops only those cohorts in `finally`. It never downgrades or removes live Sentinel data. Windows execution uses the existing backend/inference environments; the CI Linux image uses its current interpreter. Do not rerun old phase scripts that overwrite historical evidence.

## Credentials and values

Create the namespace before credentials:

```sh
kubectl --context YOUR_CONTEXT apply -f infra/kubernetes/namespace.yaml
python scripts/create_deployment_secret.py --namespace sentinel-demo
kubectl --context YOUR_CONTEXT apply -f .runtime/deployment-secret.json
```

The credential helper prompts for a Console password, writes a bcrypt hash, and generates separate API, database, Grafana and demo control credentials. It uses exclusive creation under ignored `.runtime`, never prints plaintext and refuses overwrite. Keep the directory private, including appropriate NTFS permissions on Windows. Replace the generated database credential with the real managed database credential privately when using an external database. Rotating PostgreSQL/Grafana credentials also requires updating those services; changing a Secret alone does not rotate an existing stored password.

The pre-created Secret must contain `api-read-token`, `api-proxy.conf`, `htpasswd`, `postgres-password`, `grafana-password`, and `chaos-token`. `api-proxy.conf` sets the same API bearer token used by `api-read-token`. The Console receives only its bcrypt file and server-side proxy include. Demo gets only its control credential; node agents get no credentials or Kubernetes API token. Never put plaintext or base64 credentials in committed Helm values. Kubernetes Secret base64 encoding is not encryption; configure cluster encryption and appropriate operator RBAC.

A **nonsecret production values template** is:

```yaml
environment: production
imageRegistry: ghcr.io/YOUR_LOWERCASE_NAMESPACE
existingSecret: sentinel-credentials
imageDigests:
  api: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
  worker: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
  frontend: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
  agent: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
  demo: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
  inference: sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST
database:
  internal: false
  host: YOUR_MANAGED_TIMESCALE_HOST
  externalCIDR: YOUR_DATABASE_NETWORK_CIDR
  sslmode: verify-full
ingress:
  enabled: true
  className: YOUR_EXISTING_INGRESS_CLASS
  host: YOUR_HTTPS_HOSTNAME
  tlsSecret: YOUR_EXISTING_TRUSTED_TLS_SECRET
  controllerNamespace: YOUR_INGRESS_NAMESPACE
  annotations: {} # Supply this controller's HTTPS-only routing/redirect settings.
chaos:
  enabled: false
```

These placeholders intentionally fail validation. Use actual digest artifacts after successful CI. For Traefik, restricting router entrypoints to the configured `websecure` entrypoint and enabling router TLS is one option; entrypoint names and installed controller behavior must be verified in your cluster. An Ingress TLS block alone does not prove HTTP is disabled or redirected. Configure that explicitly, use the HTTPS URL, and verify the absence of an HTTP Basic-auth challenge before public use. Public Grafana is optional via `grafana.expose`; it uses its own required login and the same TLS hostname under `/grafana/`.

External PostgreSQL must provide TimescaleDB and an account permitted to run the six existing migrations. TLS hostname verification uses libpq's system trust (`PGSSLROOTCERT=system`); private CA deployments need their approved trust configuration. The in-cluster StatefulSet is for demo mode only and preserves its data PVC. Prometheus and Grafana also use retained PVCs. Application processes are single replicas with bounded process-local rates; distributed rate limiting and fleet-scale capacity are not claimed.

## Deploy and verify

With reviewed nonsecret values, existing credentials/TLS and images available:

```sh
helm upgrade --install sentinel infra/helm/sentinel \
  --kube-context YOUR_CONTEXT --namespace sentinel-demo --create-namespace \
  -f YOUR_REVIEWED_VALUES.yaml --rollback-on-failure --wait --timeout 20m
python scripts/verify_deployment.py --context YOUR_CONTEXT --namespace sentinel-demo \
  --url https://YOUR_HTTPS_HOSTNAME --output .runtime/deployment-verified.json
```

Helm 4 uses `--rollback-on-failure`; the older `--atomic` flag is not used here. API init containers apply migrations before serving. Other application pods wait for real API readiness. Inference imports verified model metadata and historical evaluation before starting, then holds the database lease; the inference rollout uses Recreate to avoid competing workers. It loads frozen artifacts before exposing its model-loaded metric. Expect at least twelve minutes of actual CPU coverage before eligible forecasts; never insert synthetic history to satisfy readiness.

The verifier checks supported server version, required Ready pods, anonymous Console denial, authenticated trusted HTTPS, API dependency readiness, real model registration/fresh score reproduction, Prometheus health, deployed agent/self scrapes and recent successful features/inference. It rejects credential redirects outside the selected HTTPS origin and requires plain HTTP to refuse connections or redirect anonymously to that origin. No TLS verification bypass exists. Supply `--grafana-url https://HOST/grafana` if exposing Grafana. Successful verification is still insufficient to claim the full phase without successful remote CI/publication and the separate safe deployed demo evidence.

If Argo CD already exists, adapt [the Application example](../infra/gitops/application.example.yaml) to the real repository, reviewed revision, values and destination and sync through that existing instance. Otherwise use Helm. The example does not install Argo CD and leaves sync/prune as operator actions.

Node agents explicitly require read-only host mounts, host PID/network scope and node-port access. Their service account has no token or RBAC. Other application components have no host mounts or Kubernetes privileges; only Prometheus can get/list/watch pods in this namespace. No component gets Kubernetes chaos permissions. NetworkPolicy enforcement depends on the CNI, and hostNetwork traffic may bypass pod policies; restrict agent port 9101 at node/firewall level and verify its reachability only from monitoring. Agent bindings use the selected node host IP. See [Kubernetes network-policy limits](https://kubernetes.io/docs/concepts/services-networking/network-policies/).

## Reproducible safe demo

Local development:

```sh
make demo
# Or:
uv run --project backend python scripts/run_demo.py
```

The existing `.env` must explicitly enable the fixed local demo, and observation/inference must be fresh. The script checks actual healthy service evidence and records the actual baseline score, even if already high. It applies one bounded 45-second latency fault, waits for objective breach and recovery, then waits for a real post-recovery feature/prediction. It records actual scores, explanation/incident/summary records and receiver-accepted alert times. It never forces a threshold crossing, fabricates a low-risk baseline, or counts an older warning as new predictive success. Evidence uses a new ignored timestamped file by default; existing files are not overwritten.

For a deployed **demo environment only**, enable the chart's fixed demo scope, use a private operator API port-forward, and set `SENTINEL_DEMO_TOKEN` from the private Secret:

```sh
kubectl --context YOUR_CONTEXT -n sentinel-demo port-forward \
  --address 127.0.0.1 service/sentinel-api 18000:8000
uv run --project backend python scripts/run_demo.py \
  --api-url http://127.0.0.1:18000 --host sentinel-demo
```

The production environment rejects chaos independently of allowlists. The public Console proxy rejects all chaos routes and every non-read API method. No new external alert/LLM/RunbookOS destinations are configured by this chart. The local receiver remains the authorized delivery scope. SHAP, incident and summary/alert output are conditional on actual fresh crossings; their absence is recorded, not filled in with fabricated evidence.

## Rollback and acceptance

Before deployment, require green remote CI/scans, reviewed image digests, private credentials/trusted certificates, available storage/CNI, a database backup, and a known previous release. Roll back on sustained readiness failures, failed auth/TLS/scrapes, stale worker success or absent fresh reproduced inference after the real warmup. A high probability alone is not a service-health or rollback signal.

Inspect `helm history sentinel --kube-context CONTEXT -n NAMESPACE`, then `helm rollback sentinel PREVIOUS_REVISION --kube-context CONTEXT -n NAMESPACE --wait --timeout 20m` when appropriate. Preserve database and monitoring PVCs. Never automatically downgrade a live database or delete volumes; restore from an approved backup if a future incompatible migration requires it. Argo-managed releases must roll back the reviewed Git revision and sync rather than be changed independently by Helm.

The original held-out recall/F1 remain zero. Controlled synthetic experiments produce real labeled degradation but do not establish generalization to production incidents. Deployment readiness and predictive usefulness are separate acceptance questions.
