import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from typing import Optional

from app.sizing import compute_recommendation

app = FastAPI(title="Resource Advisor API", version="0.2.0")

METRICS_BASE_URL = "http://localhost:8080"

_cache: dict = {}


@app.get("/services")
async def list_services():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{METRICS_BASE_URL}/services")
        return resp.json()


async def _fetch_recommendation(client: httpx.AsyncClient, svc: dict) -> dict:
    resp = await client.get(
        f"{METRICS_BASE_URL}/services/{svc['id']}/metrics"
    )
    resp.raise_for_status()
    metrics = resp.json()
    return compute_recommendation(svc, metrics)


@app.get("/recommendations")
async def list_recommendations(type: Optional[str] = None):
    async with httpx.AsyncClient() as client:
        services_resp = await client.get(f"{METRICS_BASE_URL}/services")
        services = services_resp.json()["services"]

        if type:
            services = [s for s in services if s["type"] == type]

        tasks = [_fetch_recommendation(client, svc) for svc in services]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if not isinstance(r, Exception)]


@app.get("/recommendations/{service_id}")
async def get_recommendation(service_id: str):
    if service_id in _cache:
        return _cache[service_id]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{METRICS_BASE_URL}/services/{service_id}/metrics"
            )
            resp.raise_for_status()
            metrics = resp.json()

            all_services = (await client.get(f"{METRICS_BASE_URL}/services")).json()["services"]
            svc = next(
                (s for s in all_services if s["id"] == service_id),
                {"id": service_id, "name": service_id, "type": metrics.get("type")},
            )

            rec = compute_recommendation(svc, metrics)
            _cache[service_id] = rec
            return rec

        except httpx.HTTPStatusError:
            raise HTTPException(status_code=502, detail="Metrics service error")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))