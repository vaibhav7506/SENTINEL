FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
COPY inference/linux /app/inference/linux
RUN --mount=type=cache,id=sentinel-linux-inference,target=/root/.cache/uv uv sync --project inference/linux --frozen --no-dev --no-install-package sentinel-backend

FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS runtime
WORKDIR /app
COPY backend/app /app/backend/app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
COPY --from=build /app/inference/linux /app/inference/linux
COPY inference/*.py /app/inference/
COPY training/*.py /app/training/
COPY artifacts/models/20260926T204848-mlp-1bcd8acd /app/artifacts/models/20260926T204848-mlp-1bcd8acd
COPY artifacts/datasets/1bcd8acdc1fab6f9 /app/artifacts/datasets/1bcd8acdc1fab6f9
ENV PATH="/app/inference/linux/.venv/bin:$PATH" PYTHONPATH=/app:/app/backend PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home sentinel
USER sentinel
EXPOSE 8004
CMD ["python", "-m", "inference.worker"]

FROM runtime AS test
USER root
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
RUN --mount=type=cache,id=sentinel-linux-inference,target=/root/.cache/uv uv sync --project inference/linux --frozen --no-install-package sentinel-backend
COPY backend /app/backend
COPY inference /app/inference
COPY training /app/training
COPY artifacts /app/artifacts
COPY scripts/check_phase10_database.py /app/scripts/check_phase10_database.py
CMD ["python", "-m", "pytest", "-c", "inference/pyproject.toml", "inference/tests", "--ignore=inference/tests/test_database.py", "--ignore=inference/tests/test_registration_database.py", "training/tests", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/sentinel-tests"]
