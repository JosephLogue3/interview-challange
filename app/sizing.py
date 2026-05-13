import math

LAMBDA_MEMORY_TIERS = [128, 256, 512, 1024, 1536, 2048, 3008]
COST_PER_GB_SECOND = 0.0000166667


def compute_recommendation(service: dict, metrics: dict) -> dict:
    compute_type = service["type"]  # type comes from the service object, not metrics
    if compute_type == "lambda":
        return _compute_lambda(service.get("name", service["id"]), metrics)
    elif compute_type == "eks_pod":
        return _compute_eks(service.get("name", service["id"]), metrics)
    else:
        raise ValueError(f"Unrecognised compute type: {compute_type!r}")


def _compute_lambda(service_name: str, metrics: dict) -> dict:
    avg = metrics["avg_duration_ms"]
    p95 = metrics["p95_duration_ms"]
    memory = metrics["memory_used_mb"]

    # Boundaries per spec: < 100 → 128, 100-499 → 256, 500-999 → 512, >= 1000 → 1024
    if avg < 100:
        base = 128
    elif avg < 500:
        base = 256
    elif avg < 1000:
        base = 512
    else:
        base = 1024

    # Bump up to the smallest tier that fits if memory_used_mb * 1.2 exceeds the base tier
    recommended = base
    required = memory * 1.2
    if required > base:
        for tier in LAMBDA_MEMORY_TIERS:
            if tier >= required:
                recommended = tier
                break
        else:
            recommended = LAMBDA_MEMORY_TIERS[-1]

    # Cost estimate uses p95 duration per spec (INFRA-2847)
    cost = (p95 / 1000) * (recommended / 1024) * COST_PER_GB_SECOND * 1_000_000

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

    # Spec: "round up to nearest N" = ceil(value / N) * N
    cpu_request = math.ceil(p50_cpu / 50) * 50
    cpu_limit = math.ceil(p95_cpu * 1.2 / 50) * 50
    mem_request = math.ceil(p50_mem / 64) * 64
    mem_limit = math.ceil(p95_mem * 1.3 / 64) * 64

    return {
        "service_name": service_name,
        "type": "eks_pod",
        "cpu_request_m": cpu_request,
        "cpu_limit_m": cpu_limit,
        "memory_request_mi": mem_request,
        "memory_limit_mi": mem_limit,
    }
