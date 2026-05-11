"""Thin wrappers around the ESPresense-companion REST API.

These wrap the endpoints documented under /api/state/*, /api/node/{id},
/api/device/{id}, /api/history/{deviceId}, /api/firmware/*. Each function
takes a CompanionClient and returns parsed JSON.
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.utils.companion_client import CompanionClient


# ── state ────────────────────────────────────────────────────────────────────

def get_config(client: CompanionClient) -> dict:
    """The full parsed YAML config (read-only via API)."""
    return client.get("/api/state/config")


def list_nodes(client: CompanionClient, include_telemetry: bool = True) -> list[dict]:
    return client.get("/api/state/nodes", params={"includeTele": str(include_telemetry).lower()})


def list_devices(client: CompanionClient, show_all: bool = False) -> list[dict]:
    return client.get("/api/state/devices", params={"showAll": str(show_all).lower()})


def get_locator_state(client: CompanionClient) -> dict:
    return client.get("/api/state/locator")


def get_calibration(client: CompanionClient) -> dict:
    return client.get("/api/state/calibration")


def reset_calibration(client: CompanionClient) -> dict:
    return client.post("/api/state/calibration/reset")


def get_auto_optimize(client: CompanionClient) -> dict:
    return client.get("/api/state/calibration/auto-optimize")


def set_auto_optimize(client: CompanionClient, enabled: bool) -> dict:
    return client.post("/api/state/calibration/auto-optimize", json=bool(enabled))


# ── nodes ────────────────────────────────────────────────────────────────────

def get_node(client: CompanionClient, node_id: str) -> dict:
    return client.get(f"/api/node/{node_id}")


def put_node(client: CompanionClient, node_id: str, settings: dict) -> Any:
    return client.put(f"/api/node/{node_id}", json=settings)


def restart_node(client: CompanionClient, node_id: str) -> Any:
    return client.post(f"/api/node/{node_id}/restart")


def update_node_firmware(client: CompanionClient, node_id: str, url: str) -> Any:
    return client.post(f"/api/node/{node_id}/update", json={"url": url})


def delete_node(client: CompanionClient, node_id: str) -> Any:
    return client.delete(f"/api/node/{node_id}")


# ── devices ──────────────────────────────────────────────────────────────────

def get_device(client: CompanionClient, device_id: str) -> dict:
    return client.get(f"/api/device/{device_id}")


def put_device(client: CompanionClient, device_id: str, settings: dict) -> Any:
    return client.put(f"/api/device/{device_id}", json=settings)


def delete_device(client: CompanionClient, device_id: str) -> Any:
    return client.delete(f"/api/device/{device_id}")


# ── history ──────────────────────────────────────────────────────────────────

def get_device_history(client: CompanionClient, device_id: str,
                       start: Optional[str] = None, end: Optional[str] = None) -> dict:
    if start or end:
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return client.get(f"/api/history/{device_id}/range", params=params)
    return client.get(f"/api/history/{device_id}")


# ── firmware ─────────────────────────────────────────────────────────────────

def list_firmware_types(client: CompanionClient) -> dict:
    return client.get("/api/firmware/types")
