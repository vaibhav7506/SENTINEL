"""Authenticated Prometheus query clients for local or managed metrics storage."""

import httpx

from app.core.config import Settings


def create_prometheus_client(
    settings: Settings, timeout: float, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    auth = (
        httpx.BasicAuth(
            settings.prometheus_username, settings.prometheus_password.get_secret_value()
        )
        if settings.prometheus_username
        else None
    )
    if transport is None:
        return httpx.AsyncClient(
            base_url=str(settings.prometheus_url),
            timeout=timeout,
            auth=auth,
            follow_redirects=False,
        )
    return httpx.AsyncClient(
        base_url=str(settings.prometheus_url),
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
        transport=transport,
    )
