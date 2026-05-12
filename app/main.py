import asyncio
import os
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query

from app.sizing import compute_recommendation

app = FastAPI(title="Resource Advisor API", version="0.1.0")

METRICS_BASE_URL = os.getenv("METRICS_BASE_URL", "http://localhost:8080")
METRICS_TIMEOUT_SECONDS = 5.0
METRICS_RETRY_ATTEMPTS = 2
SUPPORTED_TYPES = {"lambda", "eks_pod"}

_services_cache: list[dict] | None = None
_service_by_id_cache: dict[str, dict] = {}


def _metrics_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=METRICS_BASE_URL,
        timeout=METRICS_TIMEOUT_SECONDS,
    )


def _upstream_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=502, detail=f"Metrics service error: {exc}")


async def _get_json(client: httpx.AsyncClient, path: str) -> dict:
    try:
        response = await client.get(path)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 404:
            raise HTTPException(status_code=404, detail="Resource not found") from exc
        raise HTTPException(
            status_code=502,
            detail=f"Metrics service returned HTTP {status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Metrics service request failed: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Metrics service returned invalid JSON",
        ) from exc


def _set_services_cache(services: list[dict]) -> None:
    global _services_cache, _service_by_id_cache
    _services_cache = services
    _service_by_id_cache = {
        service["id"]: service
        for service in services
        if "id" in service
    }


def _clear_caches() -> None:
    global _services_cache, _service_by_id_cache
    _services_cache = None
    _service_by_id_cache = {}


async def _fetch_services(
    client: httpx.AsyncClient,
    *,
    use_cache: bool = True,
) -> list[dict]:
    if use_cache and _services_cache is not None:
        return _services_cache

    payload = await _get_json(client, "/services")
    services = payload.get("services")
    if not isinstance(services, list):
        raise HTTPException(
            status_code=502,
            detail="Metrics service returned an invalid services payload",
        )
    _set_services_cache(services)
    return services


async def _get_service_by_id(
    client: httpx.AsyncClient,
    service_id: str,
) -> dict | None:
    if service_id not in _service_by_id_cache:
        await _fetch_services(client)
    return _service_by_id_cache.get(service_id)


async def _fetch_metrics(client: httpx.AsyncClient, service_id: str) -> dict:
    last_error: HTTPException | None = None
    for attempt in range(METRICS_RETRY_ATTEMPTS):
        try:
            return await _get_json(client, f"/services/{service_id}/metrics")
        except HTTPException as exc:
            last_error = exc
            if exc.status_code != 502 or attempt == METRICS_RETRY_ATTEMPTS - 1:
                break
            await asyncio.sleep(0)

    raise last_error or HTTPException(
        status_code=502,
        detail="Metrics service request failed",
    )


def _validate_type_filter(service_type: Optional[str]) -> None:
    if service_type is not None and service_type not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of: {', '.join(sorted(SUPPORTED_TYPES))}",
        )


async def _recommendation_for_service(
    client: httpx.AsyncClient,
    service: dict,
) -> dict:
    metrics = await _fetch_metrics(client, service["id"])
    try:
        return compute_recommendation(service, metrics)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid metrics for service {service['id']}: {exc}",
        ) from exc


@app.get("/services")
async def list_services():
    async with _metrics_client() as client:
        try:
            services = await _fetch_services(client)
        except Exception as exc:
            raise _upstream_error(exc) from exc
    return {"services": services}


@app.get("/recommendations")
async def list_recommendations(
    service_type: Optional[str] = Query(
        default=None,
        alias="type",
        description="Filter by service type",
    ),
):
    _validate_type_filter(service_type)

    async with _metrics_client() as client:
        services = await _fetch_services(client)
        selected_services = [
            service
            for service in services
            if service_type is None or service.get("type") == service_type
        ]
        tasks = [
            _recommendation_for_service(client, service)
            for service in selected_services
        ]
        return await asyncio.gather(*tasks)


@app.get("/recommendations/{service_id}")
async def get_recommendation(service_id: str):
    async with _metrics_client() as client:
        service = await _get_service_by_id(client, service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="Service not found")

        return await _recommendation_for_service(client, service)
