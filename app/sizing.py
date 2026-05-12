import math

LAMBDA_MEMORY_TIERS = [128, 256, 512, 1024, 1536, 2048, 3008]
COST_PER_GB_SECOND = 0.0000166667


def compute_recommendation(service: dict, metrics: dict) -> dict:
    compute_type = service.get("type") or metrics.get("type")
    metrics_type = metrics.get("type")
    service_name = service.get("name", service["id"])

    if metrics_type is not None and metrics_type != compute_type:
        raise ValueError(
            f"metrics type {metrics_type!r} does not match service type {compute_type!r}"
        )

    if compute_type == "lambda":
        return _compute_lambda(service_name, metrics)
    if compute_type == "eks_pod":
        return _compute_eks(service_name, metrics)

    raise ValueError(f"Unrecognized compute type: {compute_type}")


def _compute_lambda(service_name: str, metrics: dict) -> dict:
    avg = metrics["avg_duration_ms"]
    p95 = metrics["p95_duration_ms"]
    memory = metrics["memory_used_mb"]

    if avg < 100:
        base = 128
    elif avg < 500:
        base = 256
    elif avg < 1000:
        base = 512
    else:
        base = 1024

    required_memory = max(base, memory * 1.2)
    recommended = next(
        (tier for tier in LAMBDA_MEMORY_TIERS if tier >= required_memory),
        None,
    )
    if recommended is None:
        raise ValueError(
            f"required memory {required_memory:.0f} MB exceeds max Lambda tier"
        )
    cost = (
        (p95 / 1000)
        * (recommended / 1024)
        * COST_PER_GB_SECOND
        * 1_000_000
    )

    notes = []
    if memory / recommended > 0.8:
        notes.append(
            f"Memory headroom is tight ({memory:.0f} MB used of {recommended} MB allocated)"
        )

    return {
        "service_name": service_name,
        "type": "lambda",
        "recommended_memory_mb": recommended,
        "estimated_monthly_cost_usd": round(cost, 4),
        "notes": notes,
    }


def _compute_eks(service_name: str, metrics: dict) -> dict:
    p50_cpu = metrics["p50_cpu_millicores"]
    p95_cpu = metrics["p95_cpu_millicores"]
    p50_mem = metrics["p50_memory_mb"]
    p95_mem = metrics["p95_memory_mb"]

    return {
        "service_name": service_name,
        "type": "eks_pod",
        "cpu_request_m": _round_up(p50_cpu, 50),
        "cpu_limit_m": _round_up(p95_cpu * 1.2, 50),
        "memory_request_mi": _round_up(p50_mem, 64),
        "memory_limit_mi": _round_up(p95_mem * 1.3, 64),
    }


def _round_up(value: float, increment: int) -> int:
    return math.ceil(value / increment) * increment
