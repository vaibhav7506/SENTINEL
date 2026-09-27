# Foundation dependency verification



Verified 2026-09-26 before installation using PyPI JSON and npm registry metadata (stable package versions), official release pages, and the Docker registry. Actual resolved transitive versions and hashes are recorded in uv/npm lockfiles.



| Component | Selected stable release | Verification |

| --- | --- | --- |

| Python | 3.14.7 | uv-managed CPython installation; https://www.python.org/downloads/ |

| uv | 0.12.19 | https://pypi.org/project/uv/ |

| FastAPI | 0.141.1 | https://pypi.org/project/fastapi/ |

| Pydantic | 2.13.5 | https://pypi.org/project/pydantic/ |

| Pydantic Settings | 2.15.0 | https://pypi.org/project/pydantic-settings/ |

| SQLAlchemy | 2.1.1 (asyncio extra) | https://pypi.org/project/sqlalchemy/ |

| Alembic | 1.20.0 | https://pypi.org/project/alembic/ |

| psycopg | 3.3.6 (binary extra) | https://pypi.org/project/psycopg/ |

| HTTPX | 0.28.1 | https://pypi.org/project/httpx/ |

| Uvicorn | 0.54.0 | https://pypi.org/project/uvicorn/ |

| React / React DOM | 19.3.0 | npm registry |

| TypeScript | 6.0.3 | npm registry; latest compatible with typescript-eslint 8.70.1 (peer constraint <6.1) |

| Vite | 8.3.1 | npm registry; Node ^20.19.0 or >=22.12.0 |

| Tailwind / Vite integration | 4.3.3 | npm registry |

| ESLint / JS config | 10.11.0 / 10.0.1 | npm registry; compatible plugin peer constraints verified |

| Nginx frontend runtime | 1.28.2-alpine (unprivileged) | Docker registry tag verified by successful image pull |

| Node build image | 22.22.0 | Matches installed supported Node release |

| TimescaleDB | 2.30.1-pg17 | Docker registry tag and https://github.com/timescale/timescaledb/releases |

| Prometheus | 3.15.0 | https://prometheus.io/download/ |

| Grafana | 13.2.2 | https://grafana.com/grafana/download?edition=oss |



SQLAlchemy's asyncio extra is required: the base distribution does not install greenlet automatically. Python 3.14-compatible greenlet and psycopg wheels were resolved by uv. Backend and demo container builds also resolve Linux dependencies from their cross-platform uv lockfiles.



Phase 2 additionally verified and locked psutil 7.2.2, prometheus-client 0.26.0, and types-psutil 7.2.2.20260906 through PyPI metadata before installation. Python 3.14-compatible packages were installed and checked in separate environments.



Do not install PyTorch, scikit-learn, pandas, NumPy or SHAP in Phase 1. Verify their stable Python compatibility again before their implementation phases. Do not use prerelease versions. Preserve locks and use `uv sync --frozen` / `npm ci` for reproduction; upgrade deliberately and rerun checks.


Phase 3 uses the existing locked packages and Python standard-library statistics/math for deterministic feature engineering. No ML or array dependencies were added. Model training remains deferred.

Phase 4 adds no external packages. The injector, observer, and dataset CLI use the existing locked FastAPI/httpx/SQLAlchemy/psycopg dependencies plus the Python standard library. Training libraries remain deferred.

Phase 5 verifies stable PyPI releases and Python 3.14 Windows x64 wheels: PyTorch 2.14.0 (installed official CPU wheel 2.14.0+cpu), scikit-learn 1.9.1, NumPy 2.5.3 and joblib 1.6.0. They are locked in the separate `training` environment. The PyTorch primary wheel host is pinned directly because its redirected host timed out. The CPU wheel hash is preserved in `training/uv.lock`. This training environment is explicitly Windows x64; backend Docker services keep their existing Linux locks and do not install ML dependencies in Phase 5. PyTorch binaries remain in the ignored environment/cache, while versioned trained artifacts are reviewable in the workspace.

Phase 6 adds the separately locked Windows inference project. It preserves the Phase 5 CPU wheel and training lock and installs stable SHAP 0.52.0 with compatible Python 3.14 dependencies. SHAP version metadata was verified at [PyPI](https://pypi.org/project/shap/), and the bounded explainer uses the [official PermutationExplainer contract](https://shap.readthedocs.io/en/latest/generated/shap.PermutationExplainer.html). API Docker images still exclude ML dependencies. Use `uv sync --project inference --frozen`; this lock does not claim Linux ML deployment support.

Phase 10 adds an independent Linux x86_64 CPU inference lock at `inference/linux/uv.lock`, preserving both native Windows ML locks. It pins CPython 3.14-compatible PyTorch 2.14.0+cpu from the [official CPU wheel index](https://download.pytorch.org/whl/cpu/torch/), NumPy 2.5.3, scikit-learn 1.9.1, joblib 1.6.0 and SHAP 0.52.0. Historical artifacts and the validation-selected cutoff are unchanged. Repeated training runs are exact within each platform; seven Linux calibration aggregates differ from the historical Windows evaluation by at most 3.28e-7. Classification/counts/cutoff match exactly; this does not improve the zero held-out recall/F1.

Application runtime images use digest-pinned Python 3.14.7 slim Debian 13 (Trixie). Builders use digest-pinned Python 3.14.7 Bookworm and uv 0.12.19; build tools stay out of runtime images. The frontend builds on Node 22.22.0 and runs as nonroot on digest-pinned Nginx 1.30.5 Alpine, the [verified current stable release](https://nginx.org/en/download.html). Initial runtime scans found critical vulnerabilities in older Debian/Nginx bases; the replacement bases pass the final critical scans without exemptions. Debian's [SQLite](https://security-tracker.debian.org/tracker/CVE-2025-7458) and [Perl](https://security-tracker.debian.org/tracker/CVE-2026-13221) records support the OS upgrade. This is a critical-severity gate at the recorded advisory snapshot, not a claim that all severities are absent.

Release validation uses checksum-verified Helm 4.3.0, kubeconform 0.8.0, Trivy 0.74.0 and actionlint 1.7.12. PyYAML 6.0.3 and bcrypt 5.0.0 are isolated deployment tooling in `infra/validation/requirements.txt`. GitHub action commits and base image digests are recorded under `infra/validation`. These tools and six local image builds have actual verification; remote CI, GHCR publication, attestations and cluster deployment remain pending.
