# Best Egg Engineering — Code Challenge

A junior engineer on our team built a first-pass implementation of the Resource Advisor API. It starts up and returns responses, but it has a number of problems we haven't had time to address. Your job is to review the code, identify the issues, and improve it.

**Expected time:** 30–60 minutes.

---

## What the Service Does

The Resource Advisor API integrates with an internal metrics service to produce right-sizing recommendations for AWS Lambda functions and EKS pods. Given observed performance data for a service, it returns the recommended memory (Lambda) or CPU/memory requests and limits (EKS).

---

## Setup

Copy the provided `docker-compose.yml` and `wiremock/` directory into your repo. Then:

```bash
# Install dependencies (creates .venv and installs from pyproject.toml / uv.lock)
uv sync --extra dev

# Start the mock metrics server
docker compose up -d

# Start the API
uv run uvicorn app.main:app --reload

# Run the tests
uv run pytest tests/ -v
```

The mock metrics server will be at `http://localhost:8080`.
The API will be at `http://localhost:8000` with docs at `http://localhost:8000/docs`.

---

## Mock API Reference

### `GET /services`
Returns a list of all monitored services with `id`, `name`, `type` (`lambda` or `eks_pod`), and `owner`.

### `GET /services/{service_id}/metrics`
Returns performance metrics. Shape depends on service type:

**Lambda:** `avg_duration_ms`, `p95_duration_ms`, `memory_used_mb`

**EKS pod:** `p50_cpu_millicores`, `p95_cpu_millicores`, `p50_memory_mb`, `p95_memory_mb`

> Some services are unreliable and may return `500` errors intermittently.

---

## Sizing Rules

### Lambda

| avg_duration_ms | Base memory (MB) |
|-----------------|-----------------|
| < 100           | 128             |
| 100 – 499       | 256             |
| 500 – 999       | 512             |
| ≥ 1000          | 1024            |

Valid memory tiers (MB): `128, 256, 512, 1024, 1536, 2048, 3008`

If `memory_used_mb × 1.2` exceeds the base tier, bump up to the smallest tier that fits.

Monthly cost estimate (1M invocations/month):
```
cost = (p95_duration_ms / 1000) × (recommended_memory_mb / 1024) × 0.0000166667 × 1_000_000
```

Include a headroom warning note if `memory_used_mb / recommended_memory_mb > 0.8`.

### EKS Pod

| Field               | Formula                      | Round up to    |
|---------------------|------------------------------|----------------|
| `cpu_request_m`     | `p50_cpu_millicores`         | nearest 50m    |
| `cpu_limit_m`       | `p95_cpu_millicores × 1.2`  | nearest 50m    |
| `memory_request_mi` | `p50_memory_mb`              | nearest 64 MiB |
| `memory_limit_mi`   | `p95_memory_mb × 1.3`       | nearest 64 MiB |

"Round up to nearest N" means `ceil(value / N) × N`.

---

## Your Task

Review the implementation in `app/` and `tests/`, identify the problems, and fix them. Issues span correctness, API design, async behavior, reliability, and testing — there are multiple things to find across each of those areas.

At minimum, the following should be true when you're done:

- `GET /recommendations` fetches service metrics concurrently, not sequentially
- `GET /recommendations/{service_id}` returns correct sizing output and proper HTTP errors
- All services — including unreliable ones — return a valid recommendation
- Sizing calculations match the rules above exactly
- Tests verify actual output values, not just that keys exist

---

## REVIEW.md

Include a short write-up covering:

1. Every issue you found, with a brief explanation of why it's a problem
2. How you fixed each one
3. Anything you would improve further given more time

---

## Notes

- You may use any tools you use day-to-day (e.g., Copilot, ChatGPT)
- The mock server uses [WireMock scenarios](https://wiremock.org/docs/stateful-behaviour/) — one service is designed to fail on the first call and succeed on the second
- The mock API may return fields not listed in the API Reference above. Include in your `REVIEW.md` the value of any non-standard field you observe in a metrics response
- You'll walk through your changes with us during the interview — be ready to explain every fix and discuss what you'd do next
