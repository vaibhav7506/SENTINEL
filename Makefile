.PHONY: init up down test lint typecheck build verify
init:
	python scripts/init_env.py
up:
	docker compose up --build
down:
	docker compose down
test:
	cd backend && uv run pytest
	cd agent && uv run pytest
	cd demo-service && uv run pytest
lint:
	uv run --project backend ruff check --config backend/pyproject.toml backend demo-service/app scripts training
	uv run --project agent ruff check agent
	cd frontend && npm run lint
typecheck:
	uv run --project backend mypy --config-file backend/pyproject.toml backend/app training/labels.py training/build_dataset.py
	uv run --project backend mypy --config-file backend/pyproject.toml demo-service/app
	uv run --project agent mypy --config-file agent/pyproject.toml agent/sentinel_agent
	cd frontend && npm run typecheck
build:
	cd frontend && npm run build
verify:
	python scripts/verify_stack.py

.PHONY: load telemetry
load:
	python scripts/load_demo.py
telemetry:
	python scripts/verify_telemetry.py

.PHONY: features feature-db-test
features:
	uv run --project backend python scripts/verify_features.py
feature-db-test:
	uv run --project backend python scripts/check_feature_database.py

.PHONY: chaos-init chaos-campaign dataset
chaos-init:
	python scripts/init_chaos_env.py --enable-local-demo
chaos-campaign:
	uv run --project backend python scripts/run_chaos_campaign.py
dataset:
	uv run --project backend python -m training.build_dataset

.PHONY: training-check train
training-check:
	uv run --project training python -m pytest -c training/pyproject.toml training/tests -q
	uv run --project training mypy --config-file training/pyproject.toml backend/app training/labels.py training/build_dataset.py training/runtime.py training/train.py
train:
	uv run --project training python -m training.train $(DATASET)

.PHONY: inference-check inference receiver
inference-check:
	uv run --project inference python -m pytest -c inference/pyproject.toml inference/tests/test_contracts.py -q
	uv run --project inference python scripts/check_inference_database.py
inference:
	uv run --project inference python -m inference.worker
receiver:
	uv run --project inference python scripts/run_local_webhook_receiver.py

.PHONY: demo release-chart
demo:
	uv run --project backend python scripts/run_demo.py
release-chart:
	python scripts/verify_release_chart.py
