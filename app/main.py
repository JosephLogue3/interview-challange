import requests
from fastapi import FastAPI
from typing import Optional

from app.sizing import compute_recommendation

app = FastAPI(title="Resource Advisor API", version="0.1.0")

METRICS_BASE_URL = "http://localhost:8080"

# Grows forever; stale data is served indefinitely; shared across all requests.
_cache: dict = {}


@app.get("/services")
async def list_services():
    resp = requests.get(f"{METRICS_BASE_URL}/services")
    return resp.json()


@app.get("/recommendations")
async def list_recommendations(type: Optional[str] = None):
    services_resp = requests.get(f"{METRICS_BASE_URL}/services")
    services = services_resp.json()["services"]

    results = []
    for svc in services:
        if type and svc["type"] != type:
            continue

        try:
            resp = requests.get(
                f"{METRICS_BASE_URL}/services/{svc['id']}/metrics"
            )
            metrics = resp.json()
            rec = compute_recommendation(svc, metrics)
            results.append(rec)
        except:
            pass  # silently swallows all errors, including 500s from svc-flaky

    return results


@app.get("/recommendations/{service_id}")
async def get_recommendation(service_id: str):
    if service_id in _cache:
        return _cache[service_id]

    try:
        resp = requests.get(
            f"{METRICS_BASE_URL}/services/{service_id}/metrics"
        )
        # compute_recommendation will silently produce a nonsense result or KeyError
        metrics = resp.json()

        # Fetch the service list just to get the display name
        all_services = requests.get(f"{METRICS_BASE_URL}/services").json()["services"]
        svc = next(
            (s for s in all_services if s["id"] == service_id),
            {"id": service_id, "name": service_id, "type": metrics.get("type")},
        )

        rec = compute_recommendation(svc, metrics)
        _cache[service_id] = metrics
        return rec

    except Exception as e:
        return {"error": str(e)}
