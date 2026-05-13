import pytest

from app.sizing import _compute_eks, _compute_lambda, compute_recommendation

"""
Tests for the sizing logic

Run with: pytest tests/ -v
"""


def test_compute_lambda():
    """Lambda sizing returns correct values and no note when memory headroom is safe"""
    result = _compute_lambda(
        "test-fn",
        {
            "avg_duration_ms": 5,
            "p95_duration_ms": 100,
            "memory_used_mb": 0,
        },
    )

    assert result == {
        "service_name": "test-fn",
        "type": "lambda",
        "recommended_memory_mb": 128,
        "estimated_monthly_cost_usd": 0.2083,
        "notes": [],
    }


def test_compute_lambda_bumps_recommended_memory():
    """Lambda sizing bumps memory when memory_used_mb x 1.2 > current tier"""
    result = _compute_lambda(
        "test-fn",
        {
            "avg_duration_ms": 100,
            "p95_duration_ms": 160,
            "memory_used_mb": 430,
        },
    )

    assert result == {
        "service_name": "test-fn",
        "type": "lambda",
        "recommended_memory_mb": 1024,
        "estimated_monthly_cost_usd": 2.6667,
        "notes": [],
    }


def test_lambda_sends_memory_headroom_note():
    """Lambda sizing emits a note when headroom memory is too low"""
    result = _compute_lambda(
        "payment-processor",
        {
            "avg_duration_ms": 85,
            "p95_duration_ms": 140,
            "memory_used_mb": 105,
        },
    )

    assert result == {
        "service_name": "payment-processor",
        "type": "lambda",
        "recommended_memory_mb": 128,
        "estimated_monthly_cost_usd": 0.2917,
        "notes": [
            "Memory headroom is tight (105 MB used of 128 MB allocated)",
        ],
    }


def test_lambda_duration_boundaries():
    """Lambda duration boundaries match the README tiers exactly."""

    # test cases: [(avg_duration_ms, expected_memory)]
    cases = [
        (99.99, 128),
        (100, 256),
        (499.99, 256),
        (500, 512),
        (999.99, 512),
        (1000, 1024),
    ]

    for avg_duration_ms, expected_memory in cases:
        result = _compute_lambda(
            "boundary-fn",
            {
                "avg_duration_ms": avg_duration_ms,
                "p95_duration_ms": 120,
                "memory_used_mb": 40,
            },
        )

        assert result["recommended_memory_mb"] == expected_memory


def test_lambda_rejects_memory_above_largest_tier():
    """Lambda sizing rejects workloads whose 20% memory headroom exceeds every valid tier."""
    with pytest.raises(ValueError, match="exceeds max Lambda tier"):
        _compute_lambda(
            "too-large",
            {
                "avg_duration_ms": 10,
                "p95_duration_ms": 20,
                "memory_used_mb": 3000,
            },
        )


def test_eks_sizing_rounds_correctly():
    """EKS sizing rounds metrics up correctly"""
    result = _compute_eks(
        "user-profile-api",
        {
            "p50_cpu_millicores": 180,
            "p95_cpu_millicores": 350,
            "p50_memory_mb": 400,
            "p95_memory_mb": 600,
        },
    )

    assert result == {
        "service_name": "user-profile-api",
        "type": "eks_pod",
        "cpu_request_m": 200,
        "cpu_limit_m": 450,
        "memory_request_mi": 448,
        "memory_limit_mi": 832,
    }


def test_compute_recommendation_processes_lambda_type_correctly():
    """Recommendation dispatch for the service lambda type returns Lambda output."""
    result = compute_recommendation(
        {"id": "svc-001", "name": "payment-processor", "type": "lambda"},
        {
            "service_id": "svc-001",
            "type": "lambda",
            "avg_duration_ms": 85,
            "p95_duration_ms": 140,
            "memory_used_mb": 105,
        },
    )

    assert result["type"] == "lambda"
    assert result["service_name"] == "payment-processor"
    assert result["recommended_memory_mb"] == 128


def test_compute_recommendation_processes_eks_type_correctly():
    """Recommendation dispatch for the service EKS type returns EKS output."""
    result = compute_recommendation(
        {"id": "svc-002", "name": "user-profile-api", "type": "eks_pod"},
        {
            "service_id": "svc-002",
            "type": "eks_pod",
            "p50_cpu_millicores": 180,
            "p95_cpu_millicores": 350,
            "p50_memory_mb": 400,
            "p95_memory_mb": 600,
        },
    )

    assert result == {
        "service_name": "user-profile-api",
        "type": "eks_pod",
        "cpu_request_m": 200,
        "cpu_limit_m": 450,
        "memory_request_mi": 448,
        "memory_limit_mi": 832,
    }


def test_compute_recommendation_rejects_mismatched_type():
    """Recommendation dispatch rejects conflicting service and metrics compute types."""
    with pytest.raises(ValueError, match="does not match"):
        compute_recommendation(
            {"id": "svc-001", "name": "payment-processor", "type": "lambda"},
            {
                "service_id": "svc-001",
                "type": "eks_pod",
                "avg_duration_ms": 85,
                "p95_duration_ms": 140,
                "memory_used_mb": 105,
            },
        )


def test_compute_recommendation_rejects_unknown_type():
    """Recommendation dispatch raises a clear error for unsupported compute types."""
    with pytest.raises(ValueError, match="Unrecognized compute type"):
        compute_recommendation(
            {"id": "svc-unknown", "name": "mystery", "type": "batch"},
            {"service_id": "svc-unknown", "type": "batch"},
        )
