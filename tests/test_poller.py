import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from sentinel.poller import Poller, HealthResult
from sentinel.config import ServiceConfig, Provider, HealthStatus


class MockResponse:
    def __init__(self, status: int):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class MockSession:
    def __init__(self, response: MockResponse):
        self.response = response

    def get(self, url):
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class TestPoller:
    @pytest.fixture
    def poller(self):
        return Poller(timeout_seconds=5.0, concurrent_limit=2)

    @pytest.fixture
    def services(self):
        return [
            ServiceConfig(
                name="svc1",
                url="https://svc1.example.com/health",
                expected_status=200,
                expected_latency_ms=500,
                provider=Provider.CLOUDFLARE,
                provider_resource_id="id1",
            ),
            ServiceConfig(
                name="svc2",
                url="https://svc2.example.com/health",
                expected_status=200,
                expected_latency_ms=500,
                provider=Provider.VERCEL,
                provider_resource_id="id2",
            ),
        ]

    @pytest.mark.asyncio
    async def test_poll_all_success(self, poller, services):
        mock_session = MockSession(MockResponse(200))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            results = await poller.poll_all(services)

        assert len(results) == 2
        for r in results:
            assert r.status == HealthStatus.SUCCESS
            assert r.status_code == 200
            assert r.latency_ms is not None
            assert r.latency_ms > 0

    @pytest.mark.asyncio
    async def test_poll_all_non_2xx(self, poller, services):
        mock_session = MockSession(MockResponse(500))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            results = await poller.poll_all(services)

        assert len(results) == 2
        for r in results:
            assert r.status == HealthStatus.NON_2XX
            assert r.status_code == 500

    @pytest.mark.asyncio
    async def test_poll_all_timeout(self, poller, services):
        class TimeoutResponse:
            async def __aenter__(self):
                raise asyncio.TimeoutError()

            async def __aexit__(self, *args):
                return None

        class TimeoutSession:
            def get(self, url):
                return TimeoutResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        with patch("aiohttp.ClientSession", return_value=TimeoutSession()):
            results = await poller.poll_all(services)

        assert len(results) == 2
        for r in results:
            assert r.status == HealthStatus.TIMEOUT
            assert r.error == "Request timeout"

    @pytest.mark.asyncio
    async def test_poll_all_connection_refused(self, poller, services):
        from aiohttp import ClientConnectorError
        error = ClientConnectorError(SimpleNamespace(host="test", port=443, ssl=True), OSError(111, "Connection refused"))

        class ErrorResponse:
            async def __aenter__(self):
                raise error

            async def __aexit__(self, *args):
                return None

        class ErrorSession:
            def get(self, url):
                return ErrorResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        with patch("aiohttp.ClientSession", return_value=ErrorSession()):
            results = await poller.poll_all(services)

        assert len(results) == 2
        for r in results:
            assert r.status == HealthStatus.CONNECTION_REFUSED

    @pytest.mark.asyncio
    async def test_poll_all_dns_failure(self, poller, services):
        from aiohttp import ClientConnectorError
        error = ClientConnectorError(SimpleNamespace(host="test", port=443, ssl=True), OSError(-2, "Name or service not known"))

        class ErrorResponse:
            async def __aenter__(self):
                raise error

            async def __aexit__(self, *args):
                return None

        class ErrorSession:
            def get(self, url):
                return ErrorResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        with patch("aiohttp.ClientSession", return_value=ErrorSession()):
            results = await poller.poll_all(services)

        assert len(results) == 2
        for r in results:
            assert r.status == HealthStatus.DNS_FAILURE

    @pytest.mark.asyncio
    async def test_concurrent_limit(self, poller, services):
        call_count = {"count": 0}

        class CountingResponse:
            def __init__(self):
                self.status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        class CountingSession:
            def get(self, url):
                call_count["count"] += 1
                return CountingResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        with patch("aiohttp.ClientSession", return_value=CountingSession()):
            await poller.poll_all(services)

        assert call_count["count"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])