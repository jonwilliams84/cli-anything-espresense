"""Node listing / renaming / point editing inside the espresense config.yaml,
plus helpers that fuse the static config with live state from the companion API.

Renaming a node here changes its `name:` in config.yaml (which the companion
treats as the MQTT node-id, alongside the slug). To rename the physical ESP
device's MQTT hostname over-the-air, see core/node_direct.py.
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


def list_config_nodes(parsed: Any) -> list[dict]:
    """Nodes as declared in config.yaml (the source-of-truth for room mapping)."""
    out: list[dict] = []
    for node in parsed.get("nodes") or []:
        out.append({
            "name": node.get("name"),
            "room": (node.get("room") or "").strip(),
            "room_raw": node.get("room"),
            "point": list(node.get("point") or []),
            "floors": list(node.get("floors") or []),
            "enabled": node.get("enabled", True),
            "stationary": node.get("stationary", True),
        })
    return out


def list_live_nodes(client: CompanionClient, include_telemetry: bool = True) -> list[dict]:
    """Nodes as the companion currently sees them (online flag, IP, RSSI...)."""
    raw = companion_api.list_nodes(client, include_telemetry=include_telemetry)
    out: list[dict] = []
    for n in raw:
        tel = n.get("telemetry") or {}
        loc = n.get("location") or {}
        out.append({
            "id": n.get("id"),
            "name": n.get("name"),
            "online": n.get("online"),
            "ip": tel.get("ip"),
            "uptime": tel.get("uptime"),
            "rssi": tel.get("rssi"),
            "firmware": tel.get("firmware"),
            "version": tel.get("version"),
            "free_heap": tel.get("freeHeap"),
            "floors": n.get("floors") or [],
            "x": loc.get("x"),
            "y": loc.get("y"),
            "z": loc.get("z"),
            "source": n.get("sourceType"),
        })
    return out


def merged_view(parsed: Any, live: list[dict]) -> list[dict]:
    """Join static config rows with live state by node name."""
    by_name = {row.get("name"): row for row in live}
    rows: list[dict] = []
    for cfg_row in list_config_nodes(parsed):
        live_row = by_name.pop(cfg_row["name"], {}) or {}
        rows.append({
            **cfg_row,
            "online": live_row.get("online"),
            "ip": live_row.get("ip"),
            "rssi": live_row.get("rssi"),
            "uptime": live_row.get("uptime"),
            "firmware": live_row.get("firmware"),
            "version": live_row.get("version"),
            "source": live_row.get("source"),
        })
    # nodes that exist live but aren't in config.yaml (e.g. autodiscovered)
    for name, row in by_name.items():
        rows.append({
            "name": name,
            "room": None,
            "room_raw": None,
            "point": [row.get("x"), row.get("y"), row.get("z")],
            "floors": row.get("floors"),
            "enabled": True,
            "stationary": True,
            "online": row.get("online"),
            "ip": row.get("ip"),
            "rssi": row.get("rssi"),
            "uptime": row.get("uptime"),
            "firmware": row.get("firmware"),
            "version": row.get("version"),
            "source": row.get("source") or "Live",
        })
    return rows


def rename_in_config(parsed: Any, old: str, new: str) -> dict:
    """Rename one node's `name:` in config.yaml. Returns {found, before, after}."""
    for node in parsed.get("nodes") or []:
        if node.get("name") == old:
            node["name"] = new
            return {"found": True, "before": old, "after": new}
    return {"found": False, "before": None, "after": None}


def set_point(parsed: Any, name: str, point: list) -> dict:
    """Set the 3D point of a node by name."""
    for node in parsed.get("nodes") or []:
        if node.get("name") == name:
            before = list(node.get("point") or [])
            node["point"] = list(point)
            return {"found": True, "before": before, "after": list(point)}
    return {"found": False, "before": None, "after": None}


def remove(parsed: Any, name: str) -> bool:
    """Remove a node by name from config.yaml. Returns True if removed."""
    nodes = parsed.get("nodes") or []
    for i, node in enumerate(nodes):
        if node.get("name") == name:
            del nodes[i]
            return True
    return False
