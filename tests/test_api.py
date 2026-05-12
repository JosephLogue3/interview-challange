import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

"""
Tests for the API

Run with: pytest tests/ -v
"""

SERVICES = [
    {
        "id": "svc-001",
        "name": "payment-processor",
        "type": "lambda",
        "owner": "payments-team",
    },
    {
        "id": "svc-002",
        "name": "user-profile-api",
        "type": "eks_pod",
        "owner": "platform-team",
    },
    {
        "id": "svc-003",
        "name": "image-resizer",
        "type": "lambda",
        "owner": "media-team",
    },
    {
        "id": "svc-004",
        "name": "analytics-worker",
        "type": "eks_pod",
        "owner": "data-team",
    },
    {
        "id": "svc-flaky",
        "name": "legacy-gateway",
        "type": "lambda",
        "owner": "platform-team",
    },
]

METRICS = {
    "svc-001": {
        "service_id": "svc-001",
        "type": "lambda",
        "avg_duration_ms": 85,
        "p95_duration_ms": 140,
        "memory_used_mb": 105,
    },
    "svc-002": {
        "service_id": "svc-002",
        "type": "eks_pod",
        "p50_cpu_millicores": 180,
        "p95_cpu_millicores": 350,
        "p50_memory_mb": 400,
        "p95_memory_mb": 600,
    },
    "svc-003": {
        "service_id": "svc-003",
        "type": "lambda",
        "avg_duration_ms": 1400,
        "p95_duration_ms": 2100,
        "memory_used_mb": 800,
        "_x_submission_token": "BEENG-2026-DELTA",
    },
    "svc-004": {
        "service_id": "svc-004",
        "type": "eks_pod",
        "p50_cpu_millicores": 620,
        "p95_cpu_millicores": 1100,
        "p50_memory_mb": 800,
        "p95_memory_mb": 1400,
    },
    "svc-flaky": {
        "service_id": "svc-flaky",
        "type": "lambda",
        "avg_duration_ms": 1500,
        "p95_duration_ms": 2200,
        "memory_used_mb": 890,
    },
}

EXPECTED_RECOMMENDATIONS = [
    {
        "service_name": "payment-processor",
        "type": "lambda",
        "recommended_memory_mb": 128,
        "estimated_monthly_cost_usd": 0.2917,
        "notes": [
            "Memory headroom is tight (105 MB used of 128 MB allocated)",
        ],
    },
    {
        "service_name": "user-profile-api",
        "type": "eks_pod",
        "cpu_request_m": 200,
        "cpu_limit_m": 450,
        "memory_request_mi": 448,
        "memory_limit_mi": 832,
    },
    {
        "service_name": "image-resizer",
        "type": "lambda",
        "recommended_memory_mb": 1024,
        "estimated_monthly_cost_usd": 35.0001,
        "notes": [],
    },
    {
        "service_name": "analytics-worker",
        "type": "eks_pod",
        "cpu_request_m": 650,
        "cpu_limit_m": 1350,
        "memory_request_mi": 832,
        "memory_limit_mi": 1856,
    },
    {
        "service_name": "legacy-gateway",
        "type": "lambda",
        "recommended_memory_mb": 1536,
        "estimated_monthly_cost_usd": 55.0001,
        "notes": [],
    },
]

EXPECTED_LAMBDA_RECOMMENDATIONS = [
    EXPECTED_RECOMMENDATIONS[0],
    EXPECTED_RECOMMENDATIONS[2],
    EXPECTED_RECOMMENDATIONS[4],
]


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        main,
        "_metrics_client",
        lambda: httpx.AsyncClient(
            transport=transport,
            base_url="http://metrics.test",
        ),
    )
    main._clear_caches()


@pytest.fixture
def fake_metrics_api(monkeypatch):
    calls = {
        "active_metrics": 0,
        "max_active_metrics": 0,
        "metrics_by_service": {},
        "services": 0,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/services":
            calls["services"] += 1
            return httpx.Response(200, json={"services": SERVICES})

        prefix = "/services/"
        suffix = "/metrics"
        if path.startswith(prefix) and path.endswith(suffix):
            service_id = path.removeprefix(prefix).removesuffix(suffix)
            calls["metrics_by_service"][service_id] = (
                calls["metrics_by_service"].get(service_id, 0) + 1
            )
            calls["active_metrics"] += 1
            calls["max_active_metrics"] = max(
                calls["max_active_metrics"],
                calls["active_metrics"],
            )
            await asyncio.sleep(0.01)
            calls["active_metrics"] -= 1

            if (
                service_id == "svc-flaky"
                and calls["metrics_by_service"][service_id] == 1
            ):
                return httpx.Response(
                    500,
                    json={
                        "error": "Internal Server Error",
                        "message": "Upstream metrics store unavailable",
                    },
                )
            if service_id in METRICS:
                return httpx.Response(200, json=METRICS[service_id])
            return httpx.Response(404, json={"error": "not found"})

        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)
    return calls


@pytest.fixture
def client(fake_metrics_api):
    return TestClient(main.app)


def test_list_services_returns_service_payload(client):
    response = client.get("/services")

    assert response.status_code == 200
    assert response.json() == {"services": SERVICES}


def test_list_recommendations_fetches_metrics_concurrently_and_retries_flaky_service(
    client,
    fake_metrics_api,
):
    response = client.get("/recommendations")

    assert response.status_code == 200
    data = response.json()
    assert data == EXPECTED_RECOMMENDATIONS
    assert fake_metrics_api["max_active_metrics"] > 1
    assert fake_metrics_api["metrics_by_service"]["svc-flaky"] == 2


def test_type_filter_returns_only_lambda_recommendations(client):
    response = client.get("/recommendations?type=lambda")

    assert response.status_code == 200
    assert response.json() == EXPECTED_LAMBDA_RECOMMENDATIONS


def test_invalid_type_filter_returns_400(client):
    response = client.get("/recommendations?type=batch")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "type must be one of: eks_pod, lambda",
    }


def test_single_recommendation_returns_exact_output(client):
    response = client.get("/recommendations/svc-002")

    assert response.status_code == 200
    assert response.json() == {
        "service_name": "user-profile-api",
        "type": "eks_pod",
        "cpu_request_m": 200,
        "cpu_limit_m": 450,
        "memory_request_mi": 448,
        "memory_limit_mi": 832,
    }


def test_single_recommendation_returns_404_for_unknown_service(client):
    response = client.get("/recommendations/svc-missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Service not found"}


def test_services_are_cached_for_follow_up_requests(client, fake_metrics_api):
    first_response = client.get("/recommendations/svc-001")
    second_response = client.get("/recommendations/svc-002")

    assert first_response.status_code == 200
    assert first_response.json() == EXPECTED_RECOMMENDATIONS[0]
    assert second_response.status_code == 200
    assert second_response.json() == EXPECTED_RECOMMENDATIONS[1]
    assert fake_metrics_api["services"] == 1


def test_services_endpoint_uses_cached_payload(client, fake_metrics_api):
    first_response = client.get("/services")
    second_response = client.get("/services")

    assert first_response.status_code == 200
    assert first_response.json() == {"services": SERVICES}
    assert second_response.status_code == 200
    assert second_response.json() == {"services": SERVICES}
    assert fake_metrics_api["services"] == 1


def test_services_returns_502_for_invalid_services_payload(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": {"id": "svc-001"}})
        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)

    response = TestClient(main.app).get("/services")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Metrics service returned an invalid services payload",
    }


def test_single_recommendation_returns_502_for_invalid_metrics(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": [SERVICES[0]]})
        if request.url.path == "/services/svc-001/metrics":
            return httpx.Response(
                200,
                json={
                    "service_id": "svc-001",
                    "type": "lambda",
                    "avg_duration_ms": 85,
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)

    response = TestClient(main.app).get("/recommendations/svc-001")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Invalid metrics for service svc-001: 'p95_duration_ms'",
    }


def test_single_recommendation_returns_502_after_retry_exhaustion(monkeypatch):
    calls = {"metrics": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": [SERVICES[0]]})
        if request.url.path == "/services/svc-001/metrics":
            calls["metrics"] += 1
            return httpx.Response(500, json={"error": "still down"})
        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)

    response = TestClient(main.app).get("/recommendations/svc-001")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Metrics service returned HTTP 500",
    }
    assert calls["metrics"] == main.METRICS_RETRY_ATTEMPTS
