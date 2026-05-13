#!/usr/bin/env python3
"""
Resource Advisor API — demonstration script.

Exercises every repaired endpoint and prints the results.

Usage:
    # 1. Start WireMock (if not already running):
    docker compose up -d

    # 2. Start the API server in another terminal:
    uv run uvicorn app.main:app --reload

    # 3. Run this script:
    uv run python scripts/demo.py
"""

import json
import sys

import httpx

API_BASE = "http://localhost:8000"


def section(title: str) -> None:
    bar = "=" * 64
    print(f"\n{bar}\n  {title}\n{bar}")


def display(data: object) -> None:
    print(json.dumps(data, indent=2))


def main() -> None:
    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        # ------------------------------------------------------------------ #
        # 1. List all monitored services
        # ------------------------------------------------------------------ #
        section("GET /services")
        resp = client.get("/services")
        resp.raise_for_status()
        services = resp.json()
        ids = [s["id"] for s in services.get("services", [])]
        print(f"Services found: {ids}")

        # ------------------------------------------------------------------ #
        # 2. All recommendations — fetched concurrently
        # ------------------------------------------------------------------ #
        section("GET /recommendations  (all, concurrent)")
        resp = client.get("/recommendations")
        resp.raise_for_status()
        recs = resp.json()
        print(f"Received {len(recs)} recommendation(s):")
        display(recs)

        # ------------------------------------------------------------------ #
        # 3. Filter by type
        # ------------------------------------------------------------------ #
        section("GET /recommendations?type=lambda")
        resp = client.get("/recommendations", params={"type": "lambda"})
        resp.raise_for_status()
        display(resp.json())

        section("GET /recommendations?type=eks_pod")
        resp = client.get("/recommendations", params={"type": "eks_pod"})
        resp.raise_for_status()
        display(resp.json())

        # ------------------------------------------------------------------ #
        # 4. Individual service recommendations
        # ------------------------------------------------------------------ #
        for sid in ["svc-001", "svc-002", "svc-003", "svc-004", "svc-flaky"]:
            section(f"GET /recommendations/{sid}")
            resp = client.get(f"/recommendations/{sid}")
            resp.raise_for_status()
            display(resp.json())

        # ------------------------------------------------------------------ #
        # 5. 404 for an unknown service
        # ------------------------------------------------------------------ #
        section("GET /recommendations/nonexistent  (expect 404)")
        resp = client.get("/recommendations/nonexistent")
        print(f"HTTP status: {resp.status_code}")
        display(resp.json())
        assert resp.status_code == 404, "Expected 404 for unknown service"

        print("\n\nAll checks passed.")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"\nERROR: Could not connect to API at {API_BASE}")
        print("Make sure the server is running:")
        print("    uv run uvicorn app.main:app --reload")
        sys.exit(1)
