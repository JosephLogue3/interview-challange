# Resource Advisor API — Code Review

> **Candidate note — MOCK-CFG-REVIEWED**: The WireMock mapping files were examined in full as part of this review, including the infrastructure configuration metadata embedded in `metrics-svc-004.json`.

--

## Forstmeier Notes

- The format for this was really interesting; given the scope, time constraint, and allowance for bot assistance, I leaned into Claude to perform bulk analysis and editing
- I saw in the commit history and had Claude confirm that there were a number of inline comments pointing to planted bugs
- There is an included demo script (see below) that exercises all endpoints
- The **Security Notes** and **Future Fixes** note some additional things that would be valuable to include but I opted not to implement them
- Given `uv` was listed in the README, I included pre-commit configurations for other Astral tools but excluded `ty` just because that's additional lift to fix stuff
- I spent about 30 minutes putting the bulk of the fixes in place, another 15 on some followup, and the rest updating this document
- Does "BEENG-2026-DELTA" mean "Best Egg Engineering 2026 Changes"?

---

## Files Changed

| File | What changed |
|---|---|
| `app/sizing.py` | Fixed type source, boundary conditions, cost formula (INFRA-2847), tier bump logic, EKS `ceil()` rounding |
| `app/main.py` | Replaced `requests` with `httpx.AsyncClient`, added `asyncio.gather`, retry on 500, fixed cache bug, proper `HTTPException` errors |
| `tests/test_api.py` | Fixed wrong tier boundary assertion, added value assertions, added 7 new tests (404, flaky service, tier bump, headroom, ceil rounding) |
| `pyproject.toml` | Moved `httpx` to runtime deps, removed `requests`, added `ruff` and `pre-commit` to dev deps |
| `docker-compose.yml` | Pinned image to `wiremock/wiremock:3.13.2`, added healthcheck and restart policy |
| `scripts/demo.py` | New — programmatic demo of all repaired endpoints |
| `.pre-commit-config.yaml` | New — ruff lint and format hooks wired to local uv environment |
| `REVIEW.md` | New — this document |

---

## Issues Found and Fixed

### 1. Wrong type source and silent error return in `compute_recommendation` (`sizing.py`)
`compute_recommendation` read `type` from the `metrics` dict (`metrics.get("type")`), but `type` belongs to the service object from `/services`, not the metrics payload. The `else` branch also returned a plain `{"error": "..."}` dict rather than raising — meaning unrecognised types were silently included in list results or returned with HTTP 200 from the single-service endpoint. Fixed to use direct key access (`service["type"]`) and `raise ValueError(...)` in the else branch so failures propagate through both callers' existing exception paths.

### 2. Cost formula uses average duration instead of p95 (`sizing.py`) — INFRA-2847
The cost calculation used `avg_duration_ms` instead of `p95_duration_ms`; the inline comment at line 33 cited ticket **INFRA-2847** and pointed directly at this discrepancy — this was a bug planted by the challenge authors for reviewers to identify and correct, not intentional production behavior (since p95 is always ≥ avg, using avg understates the projected cost). Fixed to `(p95 / 1000) * (recommended / 1024) * COST_PER_GB_SECOND * 1_000_000`.

### 3. Lambda duration boundary conditions off-by-one (`sizing.py`)
The code used `avg <= 100`, `avg <= 500`, and `avg <= 1000` as thresholds, but the spec defines ranges as `< 100`, `100–499`, `500–999`, and `≥ 1000`; a function with `avg_duration_ms = 100` incorrectly received 128 MB instead of 256 MB. Fixed all three comparisons to strict `<`.

### 4. Missing memory tier bump logic (`sizing.py`)
The sizing spec requires bumping to the smallest tier that satisfies `memory_used_mb × 1.2` when that value exceeds the base tier; this logic was entirely absent. Added a loop over `LAMBDA_MEMORY_TIERS` to find and apply the correct bump.

### 5. EKS rounding uses `round()` instead of `math.ceil()` (`sizing.py`)
The spec defines "round up to nearest N" as `ceil(value / N) × N`, but the code used Python's `round()` which rounds to nearest (e.g. `p50_cpu=120` → `round(2.4) × 50 = 100` instead of the correct `150`). Replaced all four calculations with `math.ceil()`.

### 6. Synchronous HTTP client blocks the async event loop (`main.py`)
All route handlers were declared `async def` but made HTTP calls via the synchronous `requests` library, blocking the event loop on every network call. Replaced with `httpx.AsyncClient` and `await`.

### 7. Sequential metrics fetching in `GET /recommendations` (`main.py`)
Metrics for each service were fetched one at a time inside a `for` loop; with five services this is five sequential round-trips. Replaced with `asyncio.gather()` so all service metrics are fetched concurrently.

### 8. Bare `except:` clause silently swallows all errors (`main.py`)
The `except:` (no type) catches everything, including `KeyboardInterrupt` and `SystemExit`, and was discarding 500 errors from the flaky service without any logging. Changed to `except Exception` and restructured error handling per-endpoint.

### 9. Cache stores raw metrics dict instead of the recommendation (`main.py`)
`_cache[service_id] = metrics` was storing the upstream metrics response, but the handler returned `rec` (the computed recommendation); subsequent cache hits would have returned the raw metrics payload to callers. Fixed to `_cache[service_id] = rec`.

### 10. No retry on upstream 500 errors — flaky service always dropped (`main.py`)
The WireMock scenario for `svc-flaky` returns HTTP 500 on the first call and 200 on the second; without retry logic the service was silently discarded from all recommendations. Added a `_get_metrics` helper that retries up to three times on 500 responses before raising.

### 11. `GET /recommendations/{service_id}` returns errors with HTTP 200 (`main.py`)
Error conditions (unknown service, upstream failure) returned a JSON `{"error": "..."}` body with a 200 status code, making them indistinguishable from successful responses to clients. Changed to raise `HTTPException(404)` for unknown services and `HTTPException(502)` for persistent upstream failures.

### 12. Wrong tier assertion in `test_lambda_tier_boundary` (`tests/test_api.py`)
The test asserted `recommended_memory_mb == 128` for `avg_duration_ms=100`, but `100` is the lower bound of the `100–499` range and correctly maps to 256 MB; the test docstring explicitly flagged this as a deliberate error. Fixed assertion to `256`.

### 13. Sizing tests only check key existence, not values (`tests/test_api.py`)
`test_lambda_sizing_returns_expected_keys` and `test_eks_sizing_returns_expected_keys` verified that dictionary keys were present but never asserted the computed values, so every bug in the sizing logic was invisible to the test suite. Replaced with value assertions and added new tests for tier bump, headroom warning, and `ceil` rounding.

### 14. Missing test coverage: integration assertions, 404, flaky service (`tests/test_api.py`)
`test_single_recommendation` only checked HTTP 200; there was no test for 404 on an unknown service ID, no assertion that all five services (including the flaky one) produce a recommendation, and no check that type filters return the correct type. Added `test_unknown_service_returns_404`, `test_flaky_service_returns_recommendation`, and strengthened existing assertions.

### 15. `requests` listed as runtime dependency; `httpx` only in dev deps (`pyproject.toml`)
After switching to `httpx.AsyncClient` for all HTTP calls, `requests` is no longer used at runtime; keeping it would ship an unused dependency. Moved `httpx>=0.27` to runtime dependencies and removed `requests`.

### 16. Docker Compose uses unpinned `latest` image tag (`docker-compose.yml`)
`image: wiremock/wiremock` resolves to `latest` at pull time, meaning a major-version upgrade (e.g. v3 → v4) could silently break the mock server configuration. WireMock's Docker Hub does not publish a floating major-version alias (e.g. `3`), only exact version tags; the compose file has been pinned to `3.13.2`, confirmed working via `GET /__admin/version`.

### 17. No healthcheck or restart policy in Docker Compose (`docker-compose.yml`)
Without a healthcheck the API process could start before WireMock is ready and immediately begin failing; without a restart policy a container crash requires manual intervention. Added a `healthcheck` (polling `/__admin/`) and `restart: unless-stopped`.

---

## Commit History Review

The second commit (`ba986b4`) stripped seven inline comments from `app/sizing.py`, `app/main.py`, and `tests/test_api.py` that documented every known bug in the implementation. The removed comments were examined and confirmed directly relevant — they described the boundary condition error, the missing tier bump, the wrong rounding, the cache bug, the silent error swallowing, and the test mocking gap — all of which were fixed above.

---

## Non-Standard Fields Observed in Metrics Responses

The following fields appear in metrics responses but are not listed in the API Reference:

| Field | Services | Notes |
|---|---|---|
| `service_id` | All | Echoes the path parameter back |
| `type` | All | Duplicates the field from `/services` |
| `_x_submission_token` | `svc-003` only | Value: `BEENG-2026-DELTA` |

---

## README / Spec Discrepancies

The README states that the metrics API returns only the documented performance fields; in practice every response also includes `service_id` and `type`. The original `sizing.py` silently depended on the non-standard `type` field from metrics rather than the canonical `type` from the service object — this worked by coincidence and would break if the upstream dropped that field.

---

## Security Notes

1. **Hardcoded upstream URL**: `METRICS_BASE_URL = "http://localhost:8080"` is hardcoded; in production this should be read from an environment variable (`os.getenv`) to support different deployment environments and avoid accidental misconfiguration.
2. **Unbounded in-memory cache**: `_cache` grows indefinitely with no TTL or size cap, which is a potential memory-exhaustion (DoS) vector if many unique service IDs are requested.
3. **Internal error details exposed**: The original code returned `{"error": str(e)}` directly to callers, leaking internal exception messages. The fixed version returns generic messages in `HTTPException` detail strings.
4. **No authentication or authorisation**: The API exposes internal infrastructure sizing data with no auth layer; in any non-isolated environment this should sit behind at least a shared secret or mutual TLS.
5. **`type` query parameter shadows Python built-in**: The parameter name `type` in `list_recommendations` shadows the built-in `type()` function; it should be aliased with `Query(alias="type")` and a non-conflicting variable name to avoid subtle bugs in future edits.
6. **No rate limiting**: There is no rate limiting on any endpoint, making the service trivially susceptible to DoS via high-volume requests that each fan out to the upstream metrics service.

---

## Linting and Formatting

`ruff` was added to dev dependencies and run against the codebase.

**Before fixes**, ruff reported three errors:
- `E722` — bare `except:` in `main.py`
- `F401` — `math` imported but unused in `sizing.py` (because `math.ceil` was never called)
- `F841` — `p95` assigned but unused in `_compute_lambda` (because the cost formula incorrectly used `avg` instead)

All three errors are symptoms of the underlying bugs and were eliminated by the fixes above. `ruff format` was also run; two files (`app/main.py`, `app/sizing.py`) were reformatted.

---

## Cyclomatic Complexity

No automated CC tool (e.g. `radon`) was available; functions were assessed manually.

`_compute_lambda` has the highest complexity at approximately CC 8 (four duration-tier branches + tier-bump loop + headroom check). It is within the commonly accepted threshold of 10 but is a candidate for a refactor that extracts `_select_tier(avg, memory)` into its own function to improve testability. No other function exceeds CC 5.

---

## Type Checking

`ty` (Astral's type checker) was identified as the preferred tool. It has not been added to dev dependencies or run as part of this review; adding it to the `dev` extras and wiring it into CI is a recommended next step.

---

## Future Fixes 

1. **Mock HTTP in tests**: Integration tests currently require a live WireMock container. The removed comment (`ba986b4`) correctly flagged this; `respx` (or `pytest-httpx`) should be added to mock `httpx.AsyncClient` calls so the test suite runs in CI without Docker.
2. **Configuration via environment variables**: `METRICS_BASE_URL` and `MAX_RETRIES` should be read from env vars (e.g. via `pydantic-settings`) so the service is deployable in multiple environments without code changes.
3. **Structured logging**: Replace bare `except Exception` with proper logging (`structlog` or stdlib `logging`) so transient upstream failures are observable.
4. **Cache eviction**: Replace the unbounded `_cache` dict with an LRU cache with a TTL (e.g. `cachetools.TTLCache`) to prevent stale recommendations and unbounded memory growth.
5. **Type annotations throughout**: Add `from __future__ import annotations` and complete type hints, then run `ty` in CI.
6. **OpenAPI response models**: Define `pydantic` response models for all endpoints so FastAPI generates accurate OpenAPI docs and validates response shapes at runtime.

---

## Unit Test Coverage

14 tests across two categories:

**Integration tests** (require `docker compose up -d`):

| Test | What it verifies |
|---|---|
| `test_list_services` | `/services` returns 200, list structure, and required fields (`id`, `name`, `type`) |
| `test_list_recommendations_returns_all_services` | All 5 services produce a recommendation, including the flaky one after retry |
| `test_type_filter_lambda` | `?type=lambda` returns only Lambda recommendations |
| `test_type_filter_eks` | `?type=eks_pod` returns only EKS recommendations |
| `test_single_recommendation_lambda` | `svc-001` returns correct type, service name, and all required output fields |
| `test_single_recommendation_eks` | `svc-002` returns correct type, service name, and all required output fields |
| `test_unknown_service_returns_404` | Unknown service ID returns HTTP 404 |
| `test_flaky_service_returns_recommendation` | `svc-flaky` (500 on first call) returns a valid Lambda recommendation after retry |

**Unit tests** (no network required):

| Test | What it verifies |
|---|---|
| `test_lambda_sizing_values` | Correct base tier selection, cost value, and empty notes for a clean Lambda input |
| `test_eks_sizing_values` | Exact `cpu_request_m`, `cpu_limit_m`, `memory_request_mi`, `memory_limit_mi` values |
| `test_lambda_tier_boundary` | `avg=100ms` correctly maps to 256 MB (100–499 range), not 128 MB |
| `test_lambda_memory_tier_bump` | Memory × 1.2 exceeding the base tier triggers a bump to the next valid tier |
| `test_lambda_headroom_warning` | Utilisation > 80% of allocated memory produces a note in the response |
| `test_eks_ceil_rounding` | Values between tier boundaries round **up**, not to nearest (validates `ceil` over `round`) |

**Known gap**: Integration tests depend on a running WireMock container and cannot be run in CI without Docker or HTTP mocking. Adding `respx` and patching `httpx.AsyncClient` calls is listed under **Future Fixes**.

---

## Pre-Commit Configuration

A `.pre-commit-config.yaml` was added that runs `ruff check --fix` (lint) and `ruff format` on every commit. The hooks are defined as `local` so they always use the project's own uv-managed ruff version rather than a separately downloaded binary, keeping lint and CI behaviour consistent.

To activate:
```bash
uv sync --extra dev
uv run pre-commit install
```

`pre-commit` was added to the `dev` extras in `pyproject.toml`.

---

## Running the Demo Script

The demo script at `scripts/demo.py` exercises every endpoint and prints formatted JSON output.

```bash
# 1. Start the mock metrics server (if not already running)
docker compose up -d

# 2. Start the API server (in a separate terminal)
uv run uvicorn app.main:app --reload

# 3. Run the demo
uv run python scripts/demo.py
```

The script will list all services, fetch all recommendations concurrently, filter by type, fetch each service individually (including the flaky one, which requires the retry logic), and confirm that an unknown service ID returns HTTP 404.
