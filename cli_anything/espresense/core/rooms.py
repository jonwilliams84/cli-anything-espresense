"""Room-polygon listing and renaming inside the espresense config.yaml.

A "room" here is an entry in `floors[].rooms[]` with a `name` and a polygon
(`points[][x,y]`). The companion's UI and HA exports key off this `name`.

`rename` and `rotate` always also patch every node's `room:` field that
referenced the old name, AND strip trailing whitespace on `room:` values
(a common source of "doesn't match polygon" bugs).
"""

from __future__ import annotations

from typing import Any, Optional


def list_rooms(parsed: Any, floor_id: Optional[str] = None) -> list[dict]:
    """Return [{floor_id, floor_name, room_name, point_count, has_color, node_count}]."""
    out: list[dict] = []
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        if floor_id and fid != floor_id:
            continue
        fname = fl.get("name")
        for room in fl.get("rooms") or []:
            name = room.get("name")
            nodes_in = _nodes_assigned_to(parsed, name)
            out.append({
                "floor_id": fid,
                "floor_name": fname,
                "room_name": name,
                "point_count": len(room.get("points") or []),
                "has_color": "color" in room,
                "node_count": len(nodes_in),
                "node_names": [n.get("name") for n in nodes_in],
            })
    return out


def _nodes_assigned_to(parsed: Any, room_name: str) -> list[dict]:
    out: list[dict] = []
    for node in parsed.get("nodes") or []:
        r = (node.get("room") or "").strip()
        if r == (room_name or "").strip():
            out.append(node)
    return out


def rename(parsed: Any, old: str, new: str, *,
           strip_node_whitespace: bool = True) -> dict:
    """Rename one room. Updates all nodes that referenced `old`.

    Returns {floor_id, rooms_renamed, nodes_repointed, whitespace_fixes}.
    """
    if old == new:
        return {"rooms_renamed": 0, "nodes_repointed": 0,
                "whitespace_fixes": 0, "floor_id": None}
    floor_id = None
    rooms_renamed = 0
    for fl in parsed.get("floors") or []:
        for room in fl.get("rooms") or []:
            if room.get("name") == old:
                room["name"] = new
                rooms_renamed += 1
                floor_id = fl.get("id")
    nodes_repointed = 0
    whitespace_fixes = 0
    for node in parsed.get("nodes") or []:
        raw = node.get("room")
        if raw is None:
            continue
        stripped = raw.strip() if isinstance(raw, str) else raw
        if strip_node_whitespace and isinstance(raw, str) and stripped != raw:
            node["room"] = stripped
            whitespace_fixes += 1
            raw = stripped
        if raw == old:
            node["room"] = new
            nodes_repointed += 1
    return {
        "floor_id": floor_id,
        "rooms_renamed": rooms_renamed,
        "nodes_repointed": nodes_repointed,
        "whitespace_fixes": whitespace_fixes,
    }


def rotate(parsed: Any, mapping: dict[str, str], *,
           strip_node_whitespace: bool = True) -> dict:
    """Apply many renames atomically (in-memory, then return).

    Useful for room swaps: e.g. {"A":"B", "B":"A"} works without collision.
    Two-pass implementation:
      1) rename each `old` -> a unique temp sentinel
      2) rename each sentinel -> the intended `new`
    Node `room:` references are rewritten in the same passes.

    Returns a per-mapping summary plus a global `whitespace_fixes` count.
    """
    # Validation
    olds = list(mapping.keys())
    news = list(mapping.values())
    if len(set(olds)) != len(olds):
        raise ValueError("rotate: duplicate `old` key in mapping")
    if len(set(news)) != len(news):
        raise ValueError("rotate: duplicate `new` value in mapping")

    sentinels = {old: f"__ROTATE_{i}__" for i, old in enumerate(olds)}
    per_mapping: dict[str, dict] = {}
    whitespace_fixes = 0

    # Pass 1: old -> sentinel
    for old in olds:
        r = rename(parsed, old, sentinels[old],
                   strip_node_whitespace=strip_node_whitespace)
        whitespace_fixes += r["whitespace_fixes"]
        per_mapping[old] = {"rooms_renamed_p1": r["rooms_renamed"],
                             "nodes_repointed_p1": r["nodes_repointed"]}
    # Pass 2: sentinel -> new
    for old, new in mapping.items():
        r = rename(parsed, sentinels[old], new,
                   strip_node_whitespace=False)  # already stripped
        per_mapping[old]["rooms_renamed_p2"] = r["rooms_renamed"]
        per_mapping[old]["nodes_repointed_p2"] = r["nodes_repointed"]
        per_mapping[old]["new"] = new
    return {
        "whitespace_fixes": whitespace_fixes,
        "renames": per_mapping,
    }


def repoint_node(parsed: Any, node_name: str, room_name: str) -> dict:
    """Set a single node's `room:` field. Returns {found, before, after}."""
    for node in parsed.get("nodes") or []:
        if node.get("name") == node_name:
            before = node.get("room")
            node["room"] = room_name
            return {"found": True, "before": before, "after": room_name}
    return {"found": False, "before": None, "after": None}
