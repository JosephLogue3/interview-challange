"""Tests for the Resource Advisor API.

Run with: pytest tests/ -v
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

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
    """Patch main to build httpx clients with MockTransport(handler); clear service caches."""
    monkeypatch.setattr(
        main,
        "_build_metrics_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://metrics.test",
        ),
    )
    main._clear_caches()


@pytest.fixture
def fake_metrics_api(monkeypatch):
    """Mock metrics HTTP API; returns call counts (services, metrics_by_service).

    svc-flaky metrics returns 500 once then 200 for retry tests.
    active_metrics / max_active_metrics track overlapping /metrics calls.
    """
    calls = {
        "metrics_by_service": {},
        "services": 0,
        "active_metrics": 0,
        "max_active_metrics": 0,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/services":
            calls["services"] += 1
            return httpx.Response(200, json={"services": SERVICES})

        # Matches GET /services/{id}/metrics from the app under test.
        prefix = "/services/"
        suffix = "/metrics"
        if path.startswith(prefix) and path.endswith(suffix):
            service_id = path.removeprefix(prefix).removesuffix(suffix)
            # Per-call count (includes retries); used by flaky-service test.
            calls["metrics_by_service"][service_id] = (
                calls["metrics_by_service"].get(service_id, 0) + 1
            )
            # Track how many /metrics handlers are mid-flight at once; max > 1
            # proves concurrent fetches when combined with sleep(0) below.
            calls["active_metrics"] += 1
            calls["max_active_metrics"] = max(
                calls["max_active_metrics"],
                calls["active_metrics"],
            )
            # Yield so another gather task can enter this handler before we return.
            await asyncio.sleep(0)
            try:
                # First metrics attempt for svc-flaky only: app should retry and succeed.
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
            finally:
                calls["active_metrics"] -= 1

        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)
    return calls


@pytest.fixture
def client(fake_metrics_api):
    with TestClient(main.app) as test_client:
        yield test_client


def test_list_services_returns_service_payload(client):
    response = client.get("/services")

    assert response.status_code == 200
    assert response.json() == {"services": SERVICES}


def test_list_recommendations_fetches_metrics_concurrently(client, fake_metrics_api):
    """GET /recommendations overlaps in-flight /metrics calls (asyncio.gather).

    The mock yields after counting an active request; max_active_metrics > 1
    only if another /metrics enters before the first leaves.
    """
    response = client.get("/recommendations")

    assert response.status_code == 200
    assert len(response.json()) == len(SERVICES)
    assert fake_metrics_api["max_active_metrics"] > 1
    assert set(fake_metrics_api["metrics_by_service"]) == {s["id"] for s in SERVICES}


def test_list_recommendations_retries_flaky_service(
    client,
    fake_metrics_api,
):
    response = client.get("/recommendations")

    assert response.status_code == 200
    assert response.json() == EXPECTED_RECOMMENDATIONS
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


def test_services_cache_refreshes_after_ttl(monkeypatch):
    calls = {"services": 0}
    payloads = [
        {"services": [SERVICES[0]]},
        {"services": [SERVICES[1]]},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            calls["services"] += 1
            return httpx.Response(200, json=payloads[calls["services"] - 1])
        return httpx.Response(404, json={"error": "not found"})

    # A negative TTL makes the cache immediately "stale" after the first fill 
    # so the second /services call refetches upstream.
    monkeypatch.setattr(main, "SERVICES_CACHE_TTL_SECONDS", -1)
    _install_mock_transport(monkeypatch, handler)

    with TestClient(main.app) as test_client:
        first_response = test_client.get("/services")
        second_response = test_client.get("/services")

    assert first_response.status_code == 200
    assert first_response.json() == payloads[0]
    assert second_response.status_code == 200
    assert second_response.json() == payloads[1]
    assert calls["services"] == 2


def test_metrics_client_is_shared_during_app_lifespan(monkeypatch):
    calls = {"client_builds": 0, "services": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            calls["services"] += 1
            return httpx.Response(200, json={"services": SERVICES})
        return httpx.Response(404, json={"error": "not found"})

    def build_client() -> httpx.AsyncClient:
        calls["client_builds"] += 1
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://metrics.test",
        )

    main._clear_caches()
    monkeypatch.setattr(main, "_build_metrics_client", build_client)

    with TestClient(main.app) as test_client:
        first_response = test_client.get("/services")
        second_response = test_client.get("/services")

        assert first_response.status_code == 200
        assert first_response.json() == {"services": SERVICES}
        assert second_response.status_code == 200
        assert second_response.json() == {"services": SERVICES}
        assert calls == {"client_builds": 1, "services": 1}

    assert main._metrics_http_client is None


@pytest.mark.anyio
async def test_metrics_client_requires_app_lifespan():
    main._metrics_http_client = None

    with pytest.raises(RuntimeError, match="Metrics HTTP client is not initialized"):
        async with main._metrics_client():
            pass


def test_list_recommendations_returns_error_item_for_failed_service(monkeypatch):
    services = [SERVICES[0], SERVICES[1]]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": services})
        if request.url.path == "/services/svc-001/metrics":
            return httpx.Response(500, json={"error": "still down"})
        if request.url.path == "/services/svc-002/metrics":
            return httpx.Response(200, json=METRICS["svc-002"])
        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)

    with TestClient(main.app) as test_client:
        response = test_client.get("/recommendations")

    assert response.status_code == 200
    assert response.json() == [
        {
            "service_id": "svc-001",
            "service_name": "payment-processor",
            "type": "lambda",
            "error": "Metrics service returned HTTP 500",
        },
        EXPECTED_RECOMMENDATIONS[1],
    ]


def test_services_returns_502_for_invalid_services_payload(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": {"id": "svc-001"}})
        return httpx.Response(404, json={"error": "not found"})

    _install_mock_transport(monkeypatch, handler)

    with TestClient(main.app) as test_client:
        response = test_client.get("/services")

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

    with TestClient(main.app) as test_client:
        response = test_client.get("/recommendations/svc-001")

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

    with TestClient(main.app) as test_client:
        response = test_client.get("/recommendations/svc-001")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Metrics service returned HTTP 500",
    }
    assert calls["metrics"] == main.METRICS_RETRY_ATTEMPTS
