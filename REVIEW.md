# Best Egg Engineering Challenge — Code Review

I've listed approximately in order what I did. 

---

## My review and fixes

I reviewed the code and made a first round of fixes (items 1–10). The table below lists each **problem**, a **link** into the original tree where it applied, a short **description**, and the **fix** landed in this branch (or “none” where no code change was needed).

| Problem | Link | Description | Fix |
|:--------|:-----|:------------|:----|
|  1 — MOCK-CFG-REVIEWED | — | Yay, I caught it! | None (acknowledged only). |
|  2 — INFRA-2847 (Lambda cost input) | [_compute_lambda() in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L38) | Monthly cost must use **p95** duration for conservative billing, not the average; scale ms, recommended MB, and `COST_PER_GB_SECOND` consistently. | `app/sizing.py`: `_compute_lambda` derives cost from **`p95_duration_ms`**. |
|  3 — Bulk recommendation errors | [list_recommendations() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L38) | Bulk `/recommendations` should not let one failing service drop the whole response; per-service failures should become structured error objects in the list (e.g. `{"error": ...}`). | `app/main.py`: `asyncio.gather` plus **`_recommendation_or_error_object_for_service`** so each row is either a recommendation map or an error dict. |
|  4 — Tests (breadth) | [Tests in test_api.py](https://github.com/JosephLogue3/interview-challange/blob/main/tests/test_api.py) | Strengthen tests: assert full JSON payloads where practical; cover errors, caching, and sizing; keep sizing tests in a dedicated file; cover conditional branches and boundaries; replace or fix **`test_lambda_tier_boundary`**. | `tests/test_api.py` expanded (payloads, 4xx/5xx, cache TTL, mocked httpx, concurrency + retry); `tests/test_sizing.py` for sizing and **`test_lambda_duration_boundaries`**. |
|  5 — Lambda tier comparisons | [_compute_lambda() tiers in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L22-L29) | Duration tiers must use **`<`** boundaries (e.g. 100 ms belongs in the 256 MB band), not **`<=`**, per the stated bands (<100 → 128, 100–499 → 256, 500–999 → 512, ≥1000 → 1024). | `app/sizing.py`: tiers use **`< 100`**, **`< 500`**, **`< 1000`**, else; covered by **`test_lambda_duration_boundaries`**. |
|  6 — Test assertions (strict JSON) | [Assertions in test_api.py](https://github.com/JosephLogue3/interview-challange/blob/main/tests/test_api.py) | Assertions should validate complete returned JSON objects (keys and values), not just status codes or partial keys. | Same work as  3: **`tests/test_api.py`** now asserts full expected payloads on the main success paths and structured bodies on error paths. |
|  7 — Cache `/services` | [list_services() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L16) | The metrics **`/services`** response should be cached to avoid redundant network work on every request. | `app/main.py`: **`_services_cache`** with TTL (**`SERVICES_CACHE_TTL_SECONDS`**, **`time.monotonic()`**) and a lock in **`_fetch_services`**. |
|  8 — Cache id → service name | [list_recommendations() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L54-L59) | After the first successful services fetch, keep a **`{service_id: service name}`** (and related fields) map so list logic does not repeat lookups. | `app/main.py`: **`_service_by_id_cache`** rebuilt when the services cache is set; **`_get_service_by_id`** for O(1) lookup. |
|  9 — Duplication in `main.py` | [main.py routes/helpers](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py) | Extract shared pieces: one path for the metrics **`/services`** call (used from list + bulk), and shared sizing path between single- and list-recommendation flows. | `app/main.py`: shared **`_get_json`**, **`_fetch_services`**, **`_fetch_metrics`**, **`_metrics_client`**, and **`lifespan`**-scoped **`httpx.AsyncClient`**. |
|  10 — EKS rounding | [_compute_eks() in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L61-L64) | Pod memory should round with **`ceil(value / N) × N`**, not Python **`round()`**, so limits align with ceiling-to-step semantics. | `app/sizing.py`: **`round_with_ceiling`** using **`math.ceil`** (and tests in **`tests/test_sizing.py`**). |

---

## AI review

I asked an agent (Codex) to review the code and give suggestions. The table below lists each **problem**, a **link** into the tree the agent referenced (line numbers are from an **older** snapshot), a short **description**, and the **fix** landed in this branch.

| Problem | Link | Description | Fix |
|:--------|:-----|:------------|:----|
| 1 - [High] Async endpoints block the event loop | [app/main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py) (agent: ~L16, L22, L49) | `requests.get()` inside **`async def`** routes blocks the event loop, hurting FastAPI concurrency and making **`/recommendations`** slow and fragile. | Shared **`httpx.AsyncClient`** created in **`lifespan`** and used via **`_metrics_client`** / **`_get_json`**; no synchronous **`requests`** in handlers. |
| 2 - [High] **`/recommendations`** fetches metrics sequentially | [app/main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py) (agent: ~L25) | README expects concurrent per-service metrics fetches; a sequential loop is too slow and underuses async I/O. | **`asyncio.gather`** over per-service tasks in **`list_recommendations`**, with **`_fetch_metrics`** (retries) per service. |
| 3 - [High] Single-recommendation cache stores the wrong object | [app/main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py) (agent: ~L46, L62) | Cached raw metrics were returned as if they were a recommendation on later **`GET`**, so clients saw the wrong shape. | Removed that pattern; **`get_recommendation`** always builds output through **`_recommendation_for_service`** (no stale metrics-as-response cache). |
| 4 - [High] HTTP errors returned as 200 JSON bodies | [app/main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py) (agent: ~L65) | **`{"error": ...}`** in a 200 body hides real failure modes; missing resource vs upstream outage should map to proper status codes. | **`HTTPException`** from **`_get_json`** / **`_fetch_metrics`** and routes; bulk list uses per-row error dicts instead of fake “success” errors. |
| 5 - [High] Lambda sizing ignores boundaries and memory bump | [app/sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py) (agent: ~L8, L22, L31) | **`<=`** on duration tiers disagreed with README bands (e.g. 100 ms → 256 MB); **`memory_used_mb * 1.2`** headroom was not applied before choosing a tier. | Tiers use **`< 100`**, **`< 500`**, **`< 1000`**, else; **`minimum_required_memory = max(base, memory * 1.2)`** then next tier; tests in **`tests/test_sizing.py`**. |
| 6 - [Medium] Tests too weak; tied to live WireMock | [tests/test_api.py](https://github.com/JosephLogue3/interview-challange/blob/main/tests/test_api.py) (agent: ~L13) | Status-only or partial assertions miss regressions; live WireMock state makes runs flaky and non-deterministic. | **`httpx.MockTransport`** via **`_build_metrics_client`** patch; full JSON on main paths; errors, cache TTL, filter **`400`**, **`404`**, **`502`**, concurrency (**`max_active_metrics`**) and flaky retry coverage. |
| 7 - [Wiremock] Non-standard **`_x_submission_token`** on **`svc-003`** | [metrics-svc-003.json](https://github.com/JosephLogue3/interview-challange/blob/main/wiremock/mappings/metrics-svc-003.json) | Extra field looked like noise unless explicitly simulating a messy upstream; agent recommended remove or document + test. | Removed from **`wiremock/mappings/metrics-svc-003.json`** and from **`METRICS`** fixtures in **`tests/test_api.py`**. |

---

## AI changes

I asked the AI to implement its recommendations, along with the caching and test changes I recommended. 

---

## My additional updates

I reviewed the AI changes and made updates.

---

## Additional AI review

I asked the AI to review again.

---

## Additional Suggestions

I have additional suggestions of my own and also consulted AI. 

### Suggestion 1: Add security

Add authorization and validate inputs

**Fix / status:** Not implemented in this challenge branch (no auth middleware or API keys).

### Suggestion 2: Centralize env variables and other configs in settings module 

[app/main.py (line 9)](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L9) has the metric service URL hardcoded as a constant. This value should be moved to a singular settings module. 

In the future, this makes the code structure more extensible, sustainable, and testable. For example, additional constants can be added for different regions, development environments, and services. 

**Fix / status:** Partial — `METRICS_BASE_URL` and `SERVICES_CACHE_TTL_SECONDS` are read with **`os.getenv`** in `app/main.py`; there is still no dedicated `settings` / Pydantic Settings module.

### Suggestion 3: Add retries, logging, monitoring, and better error handling for Metrics service

This includes another check if we need more clear HTTP error codes returned for exceptions. 

Also recommended to add better configuration management for timeout, retry count, and metrics base URL.

**Fix / status:** Partial — **`METRICS_TIMEOUT_SECONDS`**, **`METRICS_RETRY_ATTEMPTS`**, and **`_fetch_metrics`** retries are in `app/main.py`; no logging/monitoring pipeline or env wiring for every knob.

### Suggestion 4: Add limit for cache size

Recommend to add a limit to cache size, and a replacement strategy, so that the cache doesn't take up indefinite memory

**Fix / status:** Not implemented — in-memory catalog still grows with upstream service count (no eviction cap).

### Suggestion 5: Add docstrings

Recommend to docstrings to methods and additional comments to explain confusing parts of code

**Fix / status:** Partial — docstrings added on `app/main.py` helpers and routes; selective comments in tests (e.g. negative TTL); not every function is documented.

### Suggestion 6: `type` parm name in `list_recommendations()`

Naming a parm "type" may fail Lint. This would be worth calling out in a review

If this API is not already customer-facing, recommend naming this parm like `service_type`

**Fix / status:** Not implemented — parameter remains `type` with an inline note in `app/main.py`; could switch to `service_type` + `Query(alias="type")` later.

### Suggestion 7: Test API

Mocks for test_API and WireMock can be aligned. This ensures unit test data matches what contributors see in Docker/WireMock runs, to prevent tests and local integration data from drifting apart (which can happen for example when new fields are added)

Also, the tests right now are rather brittle. If I were writing tests for Best Egg's actual repo, I would update the tests for additional safeness. Example changes include:
- Tests patch **`_build_metrics_client`** with an `httpx.AsyncClient(MockTransport(...))` factory (current `tests/test_api.py`). A stricter helper could wrap the client in **`@asynccontextmanager`** to mirror production’s `_metrics_client` protocol exactly.

**Fix / status:** Partial — fixtures mirror WireMock payloads for the main paths; full alignment / less brittle assertions are still future work.

### Suggestion 8: lint checks

Changes need to be linted

**Fix / status:** Open - This needs linting!