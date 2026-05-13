import math

LAMBDA_MEMORY_TIERS = [128, 256, 512, 1024, 1536, 2048, 3008]
COST_PER_GB_SECOND = 0.0000166667


def compute_recommendation(service: dict, metrics: dict) -> dict:
    service_type = service.get("type")
    metrics_type = metrics.get("type")
    service_name = service.get("name", service["id"])

    # type check for extra safety: service and metric type should match
    if metrics_type is not None and metrics_type != service_type:
        raise ValueError(
            f"metrics type {metrics_type!r} does not match service type {service_type!r}"
        )

    if service_type == "lambda":
        return _compute_lambda(service_name, metrics)
    if service_type == "eks_pod":
        return _compute_eks(service_name, metrics)

    raise ValueError(f"Unrecognized compute type: {service_type}")


def _compute_lambda(service_name: str, metrics: dict) -> dict:
    avg = metrics["avg_duration_ms"]
    p95 = metrics["p95_duration_ms"]
    memory = metrics["memory_used_mb"]

    if avg < 100:
        base = LAMBDA_MEMORY_TIERS[0]
    elif avg < 500:
        base = LAMBDA_MEMORY_TIERS[1]
    elif avg < 1000:
        base = LAMBDA_MEMORY_TIERS[2]
    else:
        base = LAMBDA_MEMORY_TIERS[3]

    # minimum required memory is either base or the correct memory used x 1.2
    minimum_required_memory = max(base, memory * 1.2)

    # find the smallest memory tier that fits the minimum required memory
    recommended = None
    for tier in LAMBDA_MEMORY_TIERS:
        if tier >= minimum_required_memory:
            recommended = tier
            break

    # throw error if memory_used_mb exceeds max lambda memory tier
    if recommended is None:
        raise ValueError(
            f"required memory {minimum_required_memory:.0f} MB exceeds max Lambda tier"
        )

    # calculate cost using p95 duration, recommended memory, and cost per GB second
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

    cpu_request = round_with_ceiling(p50_cpu, 50)
    cpu_limit = round_with_ceiling(p95_cpu * 1.2, 50)
    mem_request = round_with_ceiling(p50_mem, 64)
    mem_limit = round_with_ceiling(p95_mem * 1.3, 64)

    return {
        "service_name": service_name,
        "type": "eks_pod",
        "cpu_request_m": cpu_request,
        "cpu_limit_m": cpu_limit,
        "memory_request_mi": mem_request,
        "memory_limit_mi": mem_limit,
    }


def round_with_ceiling(value: float, increment: int) -> int:
    return math.ceil(value / increment) * increment
