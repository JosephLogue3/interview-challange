import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException

from app.sizing import compute_recommendation

app = FastAPI(title="Resource Advisor API", version="0.1.0")

METRICS_BASE_URL = "http://localhost:8080"

_cache: dict = {}

MAX_RETRIES = 3


async def _get_metrics(client: httpx.AsyncClient, service_id: str) -> dict:
    """Fetch metrics for a service, retrying up to MAX_RETRIES times on 500 errors."""
    for attempt in range(MAX_RETRIES):
        resp = await client.get(f"{METRICS_BASE_URL}/services/{service_id}/metrics")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code != 500 or attempt == MAX_RETRIES - 1:
            resp.raise_for_status()  # raises for non-200; always raises on last attempt
    raise RuntimeError(
        "unreachable"
    )  # MAX_RETRIES > 0 guarantees raise_for_status was called


@app.get("/services")
async def list_services():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{METRICS_BASE_URL}/services")
        return resp.json()


@app.get("/recommendations")
async def list_recommendations(type: Optional[str] = None):
    async with httpx.AsyncClient() as client:
        services_resp = await client.get(f"{METRICS_BASE_URL}/services")
        services = services_resp.json()["services"]

        if type:
            services = [s for s in services if s["type"] == type]

        async def fetch_one(svc: dict) -> Optional[dict]:
            try:
                metrics = await _get_metrics(client, svc["id"])
                return compute_recommendation(svc, metrics)
            except Exception:
                return None

        results = await asyncio.gather(*[fetch_one(svc) for svc in services])
        return [r for r in results if r is not None]


@app.get("/recommendations/{service_id}")
async def get_recommendation(service_id: str):
    if service_id in _cache:
        return _cache[service_id]

    async with httpx.AsyncClient() as client:
        try:
            metrics = await _get_metrics(client, service_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(
                    status_code=404, detail=f"Service '{service_id}' not found"
                )
            raise HTTPException(
                status_code=502,
                detail=f"Upstream metrics service error for '{service_id}'",
            )

        all_services = (await client.get(f"{METRICS_BASE_URL}/services")).json()[
            "services"
        ]
        svc = next((s for s in all_services if s["id"] == service_id), None)

        if svc is None:
            raise HTTPException(
                status_code=404, detail=f"Service '{service_id}' not found"
            )

        rec = compute_recommendation(svc, metrics)
        _cache[service_id] = rec  # cache the recommendation, not raw metrics
        return rec
