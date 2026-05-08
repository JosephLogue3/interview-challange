"""
Tests for the Resource Advisor API.

Run with: pytest tests/ -v
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_list_services():
    response = client.get("/services")
    assert response.status_code == 200


def test_list_recommendations_returns_something():
    response = client.get("/recommendations")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_type_filter():
    response = client.get("/recommendations?type=lambda")
    assert response.status_code == 200
    assert response.json() is not None


def test_lambda_sizing_returns_expected_keys():
    from app.sizing import _compute_lambda

    result = _compute_lambda("test-fn", {
        "avg_duration_ms": 50,
        "p95_duration_ms": 80,
        "memory_used_mb": 30,
    })

    assert "recommended_memory_mb" in result
    assert "estimated_monthly_cost_usd" in result
    assert "notes" in result


def test_eks_sizing_returns_expected_keys():
    from app.sizing import _compute_eks

    result = _compute_eks("test-svc", {
        "p50_cpu_millicores": 100,
        "p95_cpu_millicores": 200,
        "p50_memory_mb": 128,
        "p95_memory_mb": 256,
    })

    assert "cpu_request_m" in result
    assert "cpu_limit_m" in result
    assert "memory_request_mi" in result
    assert "memory_limit_mi" in result


def test_single_recommendation():
    response = client.get("/recommendations/svc-001")
    assert response.status_code == 200


def test_lambda_tier_boundary():
    """Checks that a function sitting exactly on a duration boundary gets the right tier.

    REVIEW NOTE: This test contains a deliberate assertion error introduced during
    the original code review. The asserted value does not match the sizing spec.
    Identify the correct expected value, fix the assertion, and note both the wrong
    value and the correct value in your REVIEW.md.
    """
    from app.sizing import _compute_lambda

    result = _compute_lambda("boundary-fn", {
        "avg_duration_ms": 100,   # sits exactly on the 100ms boundary
        "p95_duration_ms": 160,
        "memory_used_mb": 40,
    })

    # Read the sizing rules in README.md carefully before deciding if this is right.
    assert result["recommended_memory_mb"] == 128  # is this the correct tier for avg=100ms?
