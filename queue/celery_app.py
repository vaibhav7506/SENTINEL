"""Supported Python 3.13 Celery orchestration; validated ML stays on Python 3.14."""

import atexit
import json
import os
import selectors
import subprocess
import time

from celery import Celery
from redis import Redis

BROKER = os.environ["REDIS_URL"]
app = Celery("sentinel", broker=BROKER, backend=BROKER)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue=os.environ.get("SENTINEL_QUEUE", "sentinel"),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 300},
    result_backend_transport_options={"visibility_timeout": 300},
    visibility_timeout=300,
    result_expires=3600,
    task_soft_time_limit=140,
    task_time_limit=150,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    beat_schedule={
        "schedule-active-hosts": {"task": "sentinel.schedule", "schedule": 15.0},
        "deliver-account-alerts": {"task": "sentinel.deliver", "schedule": 5.0},
    },
)
bridge = None


class Bridge:
    def __init__(self):
        executable = os.environ.get("SENTINEL_ML_PYTHON", "/app/inference/linux/.venv/bin/python")
        self.process = subprocess.Popen(
            [executable, "-m", "inference.push_runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        atexit.register(self.close)

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def call(self, task):
        if self.process.poll() is not None:
            raise RuntimeError("Scoring subprocess unavailable")
        self.process.stdin.write(json.dumps(task) + "\n")
        self.process.stdin.flush()
        if not self.selector.select(timeout=120):
            self.close()
            raise RuntimeError("Scoring subprocess timed out")
        line = self.process.stdout.readline(131073)
        if len(line) > 131072:
            self.close()
            raise RuntimeError("Task reply exceeded limit")
        response = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError("Scoring task failed")
        return response["result"]


def invoke(operation, **kwargs):
    global bridge
    if bridge is None or bridge.process.poll() is not None:
        bridge = Bridge()
    redis = Redis.from_url(BROKER, socket_timeout=2, socket_connect_timeout=2)
    started = time.monotonic()
    redis.incr("sentinel:platform:tasks_started")
    try:
        result = bridge.call({"operation": operation, **kwargs})
        if isinstance(result, dict):
            for key, value in result.pop("_platform", {}).items():
                redis.incrbyfloat("sentinel:platform:" + key, value)
        redis.incr("sentinel:platform:tasks_succeeded")
        redis.set("sentinel:platform:worker_last_success", time.time())
        if operation == "infer":
            redis.incrby("sentinel:platform:predictions", result.get("predictions", 0))
        return result
    except Exception:
        redis.incr("sentinel:platform:tasks_failed")
        raise
    finally:
        redis.incrbyfloat("sentinel:platform:task_duration_sum", time.monotonic() - started)
        redis.close()


@app.task(
    name="sentinel.schedule",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def schedule():
    redis = Redis.from_url(BROKER, socket_timeout=2)
    sent = 0
    try:
        for task in invoke("due"):
            key = (
                "sentinel:scheduled:"
                + task["account_id"]
                + ":"
                + task["host_id"]
                + ":"
                + task["window_end"]
            )
            if redis.set(key, "queued", ex=180, nx=True):
                try:
                    infer.delay(**task)
                    sent += 1
                except Exception:
                    redis.delete(key)
                    raise
        return {"enqueued": sent}
    finally:
        redis.close()


@app.task(
    name="sentinel.infer",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def infer(self, account_id, host_id, window_end):
    return invoke("infer", account_id=account_id, host_id=host_id, window_end=window_end)


@app.task(
    name="sentinel.deliver",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def deliver():
    return invoke("deliver")
