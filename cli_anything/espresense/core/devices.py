"""Device tracking helpers — wrappers over the companion's device API."""

from __future__ import annotations

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


def list_devices(client: CompanionClient, show_all: bool = False) -> list[dict]:
    raw = companion_api.list_devices(client, show_all=show_all)
    out: list[dict] = []
    for d in raw:
        out.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "room": (d.get("room") or {}).get("name") if isinstance(d.get("room"), dict) else d.get("room"),
            "floor": (d.get("floor") or {}).get("name") if isinstance(d.get("floor"), dict) else d.get("floor"),
            "last_seen": d.get("lastHit") or d.get("lastSeen"),
            "x": d.get("x"),
            "y": d.get("y"),
            "z": d.get("z"),
            "confidence": d.get("confidence"),
            "ref_rssi": d.get("configuredRefRssi") or d.get("refRssi"),
            "raw": d,
        })
    return out


def get_device(client: CompanionClient, device_id: str) -> dict:
    return companion_api.get_device(client, device_id)


def update_device(client: CompanionClient, device_id: str, *,
                  name: str | None = None, ref_rssi: int | None = None,
                  anchored_x: float | None = None,
                  anchored_y: float | None = None,
                  anchored_z: float | None = None) -> dict | None:
    settings: dict = {}
    if name is not None:
        settings["name"] = name
    if ref_rssi is not None:
        settings["RefRssi"] = ref_rssi
    if anchored_x is not None:
        settings.setdefault("anchored", {})["x"] = anchored_x
    if anchored_y is not None:
        settings.setdefault("anchored", {})["y"] = anchored_y
    if anchored_z is not None:
        settings.setdefault("anchored", {})["z"] = anchored_z
    if not settings:
        return None
    return companion_api.put_device(client, device_id, settings)


def delete_device(client: CompanionClient, device_id: str) -> None:
    companion_api.delete_device(client, device_id)
