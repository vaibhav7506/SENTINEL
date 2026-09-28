FROM python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS queue-build
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
WORKDIR /queue
COPY queue/pyproject.toml queue/uv.lock ./
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.13

FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS ml-build
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
COPY inference/linux /app/inference/linux
RUN --mount=type=cache,id=sentinel-linux-inference,target=/root/.cache/uv uv sync --project inference/linux --frozen --no-dev --no-install-package sentinel-backend

FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS ml-runtime
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
COPY --from=ml-build /app/inference/linux /app/inference/linux
COPY artifacts/models/20260926T204848-mlp-1bcd8acd /app/artifacts/models/20260926T204848-mlp-1bcd8acd
COPY artifacts/datasets/1bcd8acdc1fab6f9 /app/artifacts/datasets/1bcd8acdc1fab6f9
ENV PATH="/app/inference/linux/.venv/bin:$PATH" PYTHONPATH=/app:/app/backend PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home sentinel
USER sentinel
EXPOSE 8004
CMD ["python", "-m", "inference.worker"]


FROM ml-runtime AS runtime
USER root
COPY --from=queue-build /usr/local/bin/uv /usr/local/bin/uv
COPY --from=queue-build /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=queue-build /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=queue-build /usr/local/lib/libpython3.13.so* /usr/local/lib/
COPY --from=queue-build /queue /queue
COPY backend/saas-runtime/requirements.txt /tmp/saas-requirements.txt
RUN uv pip install --python /app/inference/linux/.venv/bin/python --require-hashes -r /tmp/saas-requirements.txt
COPY backend/app /app/backend/app
COPY inference/*.py /app/inference/
COPY training/*.py /app/training/
COPY queue/*.py /queue/
ENV PYTHONPATH=/app:/app/backend:/queue SENTINEL_ML_PYTHON=/app/inference/linux/.venv/bin/python
USER sentinel
CMD ["/queue/.venv/bin/celery", "-A", "celery_app:app", "worker", "--loglevel=WARNING", "--concurrency=2"]
