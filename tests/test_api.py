"""
Tests for the Resource Advisor API.

Integration tests (test_list_*, test_single_*, test_404_*) require WireMock running:
    docker compose up -d

Run all tests with: uv run pytest tests/ -v
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Integration tests — require WireMock at localhost:8080
# ---------------------------------------------------------------------------


def test_list_services():
    response = client.get("/services")
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    assert len(data["services"]) > 0
    first = data["services"][0]
    assert "id" in first
    assert "name" in first
    assert "type" in first


def test_list_recommendations_returns_all_services():
    response = client.get("/recommendations")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # All 5 services should return a recommendation (svc-flaky succeeds after retry)
    assert len(data) == 5


def test_type_filter_lambda():
    response = client.get("/recommendations?type=lambda")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert all(r["type"] == "lambda" for r in data)


def test_type_filter_eks():
    response = client.get("/recommendations?type=eks_pod")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert all(r["type"] == "eks_pod" for r in data)


def test_single_recommendation_lambda():
    response = client.get("/recommendations/svc-001")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "lambda"
    assert data["service_name"] == "payment-processor"
    assert "recommended_memory_mb" in data
    assert "estimated_monthly_cost_usd" in data
    assert "notes" in data


def test_single_recommendation_eks():
    response = client.get("/recommendations/svc-002")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "eks_pod"
    assert data["service_name"] == "user-profile-api"
    assert "cpu_request_m" in data
    assert "cpu_limit_m" in data
    assert "memory_request_mi" in data
    assert "memory_limit_mi" in data


def test_unknown_service_returns_404():
    response = client.get("/recommendations/does-not-exist")
    assert response.status_code == 404


def test_flaky_service_returns_recommendation():
    """svc-flaky returns 500 on first call; retry logic must recover it."""
    response = client.get("/recommendations/svc-flaky")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "lambda"


# ---------------------------------------------------------------------------
# Unit tests — sizing logic, no HTTP required
# ---------------------------------------------------------------------------


def test_lambda_sizing_values():
    from app.sizing import _compute_lambda

    result = _compute_lambda(
        "test-fn",
        {"avg_duration_ms": 50, "p95_duration_ms": 80, "memory_used_mb": 30},
    )

    assert result["recommended_memory_mb"] == 128
    # cost = (80/1000) * (128/1024) * 0.0000166667 * 1_000_000
    expected_cost = round((80 / 1000) * (128 / 1024) * 0.0000166667 * 1_000_000, 4)
    assert result["estimated_monthly_cost_usd"] == expected_cost
    assert result["notes"] == []


def test_eks_sizing_values():
    from app.sizing import _compute_eks

    result = _compute_eks(
        "test-svc",
        {
            "p50_cpu_millicores": 100,
            "p95_cpu_millicores": 200,
            "p50_memory_mb": 128,
            "p95_memory_mb": 256,
        },
    )

    # cpu_request = ceil(100/50)*50 = 100
    assert result["cpu_request_m"] == 100
    # cpu_limit = ceil(200*1.2/50)*50 = ceil(4.8)*50 = 5*50 = 250
    assert result["cpu_limit_m"] == 250
    # mem_request = ceil(128/64)*64 = 2*64 = 128
    assert result["memory_request_mi"] == 128
    # mem_limit = ceil(256*1.3/64)*64 = ceil(5.2)*64 = 6*64 = 384
    assert result["memory_limit_mi"] == 384


def test_lambda_tier_boundary():
    """avg=100ms falls in the 100-499 range, so base tier is 256 MB (not 128)."""
    from app.sizing import _compute_lambda

    result = _compute_lambda(
        "boundary-fn",
        {"avg_duration_ms": 100, "p95_duration_ms": 160, "memory_used_mb": 40},
    )

    assert result["recommended_memory_mb"] == 256


def test_lambda_memory_tier_bump():
    """When memory_used_mb * 1.2 exceeds the base tier, bump to the next tier."""
    from app.sizing import _compute_lambda

    # avg=50ms → base=128; memory=120, 120*1.2=144 > 128 → bump to 256
    result = _compute_lambda(
        "bump-fn",
        {"avg_duration_ms": 50, "p95_duration_ms": 100, "memory_used_mb": 120},
    )

    assert result["recommended_memory_mb"] == 256


def test_lambda_headroom_warning():
    """A warning note is added when memory_used_mb / recommended_memory_mb > 0.8."""
    from app.sizing import _compute_lambda

    # avg=50ms → base=128; memory=105, 105*1.2=126 < 128 (no bump); 105/128=0.82 > 0.8
    result = _compute_lambda(
        "headroom-fn",
        {"avg_duration_ms": 50, "p95_duration_ms": 80, "memory_used_mb": 105},
    )

    assert result["recommended_memory_mb"] == 128
    assert len(result["notes"]) == 1
    assert "tight" in result["notes"][0]


def test_eks_ceil_rounding():
    """EKS values that fall between boundaries must round UP, not to nearest."""
    from app.sizing import _compute_eks

    # p50_cpu=120 → ceil(120/50)*50 = ceil(2.4)*50 = 3*50 = 150 (round() would give 100)
    result = _compute_eks(
        "ceil-svc",
        {
            "p50_cpu_millicores": 120,
            "p95_cpu_millicores": 200,
            "p50_memory_mb": 100,
            "p95_memory_mb": 200,
        },
    )

    assert result["cpu_request_m"] == 150
