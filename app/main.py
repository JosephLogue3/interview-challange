import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException

from app.sizing import compute_recommendation

METRICS_BASE_URL = os.getenv("METRICS_BASE_URL", "http://localhost:8080")
METRICS_TIMEOUT_SECONDS = 5.0
METRICS_RETRY_ATTEMPTS = 2
SERVICES_CACHE_TTL_SECONDS = float(os.getenv("SERVICES_CACHE_TTL_SECONDS", "60"))
SUPPORTED_TYPES = {"lambda", "eks_pod"}

_metrics_http_client: httpx.AsyncClient | None = None
_services_cache: list[dict] | None = None
_service_by_id_cache: dict[str, dict] = {}
_services_cache_expires_at = 0.0
_services_cache_lock: asyncio.Lock | None = None


def _build_metrics_client() -> httpx.AsyncClient:
    """Return a new HTTP client configured for the metrics service"""
    return httpx.AsyncClient(
        base_url=METRICS_BASE_URL,
        timeout=METRICS_TIMEOUT_SECONDS,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open a shared metrics client on app start; close and clear caches on shutdown"""
    # on start, set global metrics client 
    global _metrics_http_client
    _metrics_http_client = _build_metrics_client()

    # yield control to FastAPI after app startup complete
    try:
        yield

    # on shutdown, close client and clear caches
    finally:
        if _metrics_http_client is not None:
            await _metrics_http_client.aclose()
        _metrics_http_client = None
        _clear_caches()


app = FastAPI(title="Resource Advisor API", version="0.1.0", lifespan=lifespan)


@asynccontextmanager
async def _metrics_client():
    """Yield the shared metrics HTTP client created during app lifespan."""
    if _metrics_http_client is None:
        raise RuntimeError("Metrics HTTP client is not initialized")
    yield _metrics_http_client


async def _get_json(client: httpx.AsyncClient, path: str) -> dict:
    """GET a JSON from a request to the metrics service"""
    # raise exception if client request fails
    try:
        response = await client.get(path)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Metrics service request failed: {exc}",
        ) from exc

    # raise exception if client response contains error
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Resource not found")
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail=f"Metrics service returned HTTP {response.status_code}",
        )

    # raise exception if JSON parsing fails
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Metrics service returned invalid JSON",
        ) from exc


def _set_services_cache(services: list[dict]) -> None:
    """Store the services list, rebuild the id index, and refresh the cache expiry time."""
    global _services_cache, _service_by_id_cache, _services_cache_expires_at
    _services_cache = services
    _service_by_id_cache = {
        service["id"]: service
        for service in services
        if "id" in service
    }
    _services_cache_expires_at = time.monotonic() + SERVICES_CACHE_TTL_SECONDS


def _clear_caches() -> None:
    """Drop cached services and the per-id lookup map."""
    global _services_cache, _service_by_id_cache, _services_cache_expires_at
    _services_cache = None
    _service_by_id_cache = {}
    _services_cache_expires_at = 0.0


def _services_cache_is_fresh() -> bool:
    """Return True if an in-memory services list exists and is not expired."""
    return (
        _services_cache is not None
        and time.monotonic() < _services_cache_expires_at
    )


def _get_services_cache_lock() -> asyncio.Lock:
    """Return a process-wide asyncio lock used to dedupe concurrent service-list fetches."""
    global _services_cache_lock
    if _services_cache_lock is None:
        _services_cache_lock = asyncio.Lock()
    return _services_cache_lock


async def _fetch_services(
    client: httpx.AsyncClient,
    use_cache: bool = True,
) -> list[dict]:
    """Load services from the metrics API (or return cached list when fresh and use_cache is True)."""
    if use_cache and _services_cache_is_fresh():
        return _services_cache

    async with _get_services_cache_lock():
        if use_cache and _services_cache_is_fresh():
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
    """Return one service dict by id, or None if absent after loading the service catalog"""
    if service_id not in _service_by_id_cache:
        await _fetch_services(client)
    return _service_by_id_cache.get(service_id)


async def _fetch_metrics(client: httpx.AsyncClient, service_id: str) -> dict:
    """GET metrics JSON for a service id; retry once on transient 502 errors"""
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
    """Raise HTTP 400 if type filter is present but not lambda or eks_pod."""
    if service_type is not None and service_type not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of: {', '.join(sorted(SUPPORTED_TYPES))}",
        )


async def _recommendation_for_service(
    client: httpx.AsyncClient,
    service: dict,
) -> dict:
    """Fetch metrics and return a sizing recommendation dict; raise HTTPException on failure."""
    metrics = await _fetch_metrics(client, service["id"])
    try:
        return compute_recommendation(service, metrics)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid metrics for service {service['id']}: {exc}",
        ) from exc


async def _recommendation_or_error_object_for_service(
    client: httpx.AsyncClient,
    service: dict,
) -> dict:
    """Like _recommendation_for_service but return a structured error dict instead of raising."""
    try:
        return await _recommendation_for_service(client, service)
    except HTTPException as exc:
        return {
            "service_id": service.get("id"),
            "service_name": service.get("name", service.get("id")),
            "type": service.get("type"),
            "error": exc.detail,
        }


@app.get("/services")
async def list_services():
    """Expose the monitored service catalog from the metrics service."""
    async with _metrics_client() as client:
        services = await _fetch_services(client)
    return {"services": services}


# Note: Naming a parm "type" may fail Lint. This would be worth calling out in a review
# If this API is not already customer-facing, recommend naming this parm like "service_type"
@app.get("/recommendations")
async def list_recommendations(type: Optional[str] = None):
    """Return recommendations for all services, optionally filtered by compute type."""
    _validate_type_filter(type)

    async with _metrics_client() as client:
        services = await _fetch_services(client)
        if type is None:
            selected_services = services
        else: 
            selected_services = [svc for svc in services if svc.get("type") == type]

        tasks = [
            _recommendation_or_error_object_for_service(client, service)
            for service in selected_services
        ]
        return await asyncio.gather(*tasks)


@app.get("/recommendations/{service_id}")
async def get_recommendation(service_id: str):
    """Return a single recommendation for the given service id."""
    async with _metrics_client() as client:
        service = await _get_service_by_id(client, service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="Service not found")

        return await _recommendation_for_service(client, service)
