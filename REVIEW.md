# Best Egg Engineering Challenge — Code Review

I've listed approximately in order what I did. 

---

## My review

I reviewed the code and came up with a first round of feedback. 

### Feedback 0: MOCK-CFG-REVIEWED

This is a good Easter egg. 

### Feedback 1:  INFRA-2847

[_compute_lambda() in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L38)

Cost estimates must use the p95 duration for conservative # billing projections, not the average

Correct algorithm:
```
cost = (p95 / 1000) * (recommended / 1024) * COST_PER_GB_SECOND * 1_000_000
```

### Feedback 2: Recommendation error handling 

[list_recommendations() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L38)

There should be proper error handling for exception caught per service. For example, something like:
```
    except Exception as e:
        return {"error": str(e)}
```

This error JSON can then be added to the `results` list instead of a recommendation map when there is an exception. 

### Feedback 3: Tests

[Tests in test_api.py](https://github.com/JosephLogue3/interview-challange/blob/main/tests/test_api.py)

1. All assertions should validate all actual keys and values in returned JSON objects.
2. There should be tests for error handling
3. There should be tests for caching
4. `Sizing.py` should be in a separate test file 
5. `Sizing.py` needs tests for all conditional cases, including error handling
6. `test_lambda_tier_boundary()`

### Feedback 4: REST API making new requests 

[All GET methods in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L14-L66)

Rest methods should not make requests to its own APIs. 

### Feedback 5: Sizing Rules

[_compute_lambda() in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L22-L29)

The correct conditions should be `<` not `<=`

| avg_duration_ms | Base memory (MB) |
|-----------------|-----------------|
| < 100           | 128             |
| 100 – 499       | 256             |
| 500 – 999       | 512             |
| ≥ 1000          | 1024            |

### Feedback 6: Test assertions

[All assertions in test_api.py](https://github.com/JosephLogue3/interview-challange/blob/main/tests/test_api.py)

Assertions should validate all actual keys and values in returned JSON objects.

### Feedback 7: Caching

[list_services() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L16)

The responses to the call to the metrics API can be cached. 
```
services_resp = requests.get(f"{METRICS_BASE_URL}/services")
```

### Feedback 7.5 (8): Caching service names

[list_recommendations() in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L54-L59)

Moreover, regarding caching:

The first time that the GET service call to the metrics API is made, we should cache the names in a mapping of {service ID: service name} so that the service name can be accessed immediately. 

### Feedback 9: Redundancy in `main.py`

[Methods in main.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py)

There should be helper functions used for repeated lines of code. For example:

the [metrics API services call](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L16) can be reused in `list_services()` and `list_recommendations()`

The code in [get_recommendation()](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L45-L66) can be reused for `list_recommendations()`

### Feedback 10: Rounding

[_compute_eks() in sizing.py](https://github.com/JosephLogue3/interview-challange/blob/main/app/sizing.py#L61-L64)

The EKS Pod rounds using:
```
ceil(value / N) × N
```

So `math.ceil()`  should be used in `_compute_eks()` instead of `round()`

---

## My changes

I implemented most of my suggested changes.

I did not implement changes for caching and tests, which seemed like larger effort.

---

## AI review

I asked an agent (Codex) to review the code and give suggestions. 

### [High] Async endpoints block the event loop

app/main.py (line 16), line 22 (line 22), line 49 (line 49) use synchronous requests.get() inside async def endpoints. This defeats FastAPI concurrency and makes /recommendations slow and fragile. Use httpx.AsyncClient.

### [High] /recommendations fetches metrics sequentially

app/main.py (line 25) loops service-by-service. README explicitly requires concurrent metric fetching. Use asyncio.gather() with per-service retry/error handling.

### [High] Single recommendation cache stores the wrong object

app/main.py (line 62) caches metrics, but line 46 (line 46) returns cached data directly. A second request returns raw metrics instead of a recommendation.

### [High] HTTP errors are returned as 200 OK JSON bodies

app/main.py (line 65) returns {"error": ...} instead of raising HTTPException. Missing service should be 404; upstream failures should be 502/503; bad upstream payloads should not look successful.

### [High] Lambda sizing rules ignore memory bumping

app/sizing.py (line 22) uses <=, but README says <100, 100-499, 500-999, >=1000. So avg_duration_ms=100 should be 256, not 128. Also line 31 (line 31) never applies memory_used_mb * 1.2 tier bumping.

### [Medium] Service type source is inconsistent

app/sizing.py (line 8) trusts metrics["type"]; the canonical type is already in /services. Prefer service metadata, and validate that metrics agree if both are present.

### [Medium] Tests are too weak and depend on live WireMock state

tests/test_api.py (line 13) mostly checks status codes or key existence. Tests should assert exact sizing outputs, retry behavior, filter behavior, 404 behavior, cache behavior, and concurrent fetching with mocked httpx responses.

### [Wiremock] `svc-003` metrics include a non-standard field: `_x_submission_token = "BEENG-2026-DELTA"`.

This can be removed, unless we intentionally want WireMock to simulate extra unknown fields from a messy upstream. In this case, it should be documented  and probably have its own test that proves recommendations still work. Right now this reads more like accidental clutter than that scenario, so this should be removed.

---

## AI changes

I asked the AI to implement its recommendations, along with the caching and test changes I recommended. 

---

## Additional Suggestions

### Suggestion 1: Add security

Add authorization and validate inputs

### Suggestion 2: Move METRICS_BASE_URL to an environmental file 

[app/main.py (line 9)](https://github.com/JosephLogue3/interview-challange/blob/main/app/main.py#L9) has the metric service URL hardcoded as a constant. This value should be moved to its own environment configuration file. 

In the future, this makes the code structure more extensible, sunstainable, and testable. For example, additional constants can be added for different regions, development environments, and services. 

### Suggestion 3: Add retries, logging, and monitoring for dependency service

Also recommended to add configuration management for timeout, retry count, and metrics base URL.

### Suggestion 4: Add TTL for caching

Recommend to add a TTL to values in cache, and a most optimal caching strategy to save memory. 


