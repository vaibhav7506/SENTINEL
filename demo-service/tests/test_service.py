import httpx

from app.main import app


async def test_work_is_real_and_bounded():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/work", params={"units": 100})
        assert response.json()["checksum"] == sum(((i * i) ^ i) % 97 for i in range(100))
        assert (await client.get("/work", params={"units": 2_000_001})).status_code == 422
        metrics = (await client.get("/metrics")).text
    assert "sentinel_http_errors_total" in metrics
    assert "sentinel_http_inflight_requests 0.0" in metrics
