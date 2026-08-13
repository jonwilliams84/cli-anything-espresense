"""Room-polygon listing and renaming inside the espresense config.yaml.

A "room" here is an entry in `floors[].rooms[]` with a `name` and a polygon
(`points[][x,y]`). The companion's UI and HA exports key off this `name`.

`rename` and `rotate` always also patch every node's `room:` field that
referenced the old name, AND strip trailing whitespace on `room:` values
(a common source of "doesn't match polygon" bugs).
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.core import geometry
from cli_anything.espresense.utils import yaml_io


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
            out.append(
                {
                    "floor_id": fid,
                    "floor_name": fname,
                    "room_name": name,
                    "point_count": len(room.get("points") or []),
                    "has_color": "color" in room,
                    "node_count": len(nodes_in),
                    "node_names": [n.get("name") for n in nodes_in],
                }
            )
    return out


def _nodes_assigned_to(parsed: Any, room_name: str) -> list[dict]:
    out: list[dict] = []
    for node in parsed.get("nodes") or []:
        r = (node.get("room") or "").strip()
        if r == (room_name or "").strip():
            out.append(node)
    return out


def rename(parsed: Any, old: str, new: str, *, strip_node_whitespace: bool = True) -> dict:
    """Rename one room. Updates all nodes that referenced `old`.

    Returns {floor_id, rooms_renamed, nodes_repointed, whitespace_fixes}.
    """
    if old == new:
        return {"rooms_renamed": 0, "nodes_repointed": 0, "whitespace_fixes": 0, "floor_id": None}
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


def rotate(parsed: Any, mapping: dict[str, str], *, strip_node_whitespace: bool = True) -> dict:
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
        r = rename(parsed, old, sentinels[old], strip_node_whitespace=strip_node_whitespace)
        whitespace_fixes += r["whitespace_fixes"]
        per_mapping[old] = {
            "rooms_renamed_p1": r["rooms_renamed"],
            "nodes_repointed_p1": r["nodes_repointed"],
        }
    # Pass 2: sentinel -> new
    for old, new in mapping.items():
        r = rename(parsed, sentinels[old], new, strip_node_whitespace=False)  # already stripped
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


def add_room(
    parsed: Any,
    floor_id: str,
    name: str,
    points: list,
    *,
    color: Optional[str] = None,
) -> dict:
    """Append a new room polygon to a floor.

    Refuses to create a duplicate name, because a duplicate makes every node
    `room:` reference to that name ambiguous (see validate.DUPLICATE_ROOM_NAME).
    Returns {added, floor_id, room_name, point_count}.
    """
    if not name or not str(name).strip():
        raise ValueError("room name must be non-empty")
    name = str(name).strip()
    for fl in parsed.get("floors") or []:
        for room in fl.get("rooms") or []:
            if room.get("name") == name:
                raise ValueError(
                    f"room {name!r} already exists on floor {fl.get('id')!r}; "
                    "room names must be unique across all floors"
                )
    target = None
    for fl in parsed.get("floors") or []:
        if fl.get("id") == floor_id:
            target = fl
            break
    if target is None:
        raise KeyError(f"no floor with id={floor_id!r}")
    if target.get("rooms") is None:
        target["rooms"] = []
    room: dict = {
        "name": name,
        "points": yaml_io.flow_seq([yaml_io.flow_seq(list(pt)) for pt in points]),
    }
    if color is not None:
        room["color"] = color
    target["rooms"].append(room)
    return {
        "added": True,
        "floor_id": floor_id,
        "room_name": name,
        "point_count": len(room["points"]),
    }


def delete_room(parsed: Any, name: str) -> dict:
    """Remove a room polygon. Reports which nodes are left dangling.

    The nodes are deliberately NOT rewritten: silently blanking a node's
    `room:` would hide the consequence of the delete. The returned
    `orphaned_nodes` list tells the caller exactly what to repoint, and
    `config doctor` will flag them as dangling_room_ref until they are.
    """
    removed = 0
    floor_id = None
    for fl in parsed.get("floors") or []:
        rooms_list = fl.get("rooms") or []
        for i in range(len(rooms_list) - 1, -1, -1):
            if rooms_list[i].get("name") == name:
                del rooms_list[i]
                removed += 1
                floor_id = fl.get("id")
    orphaned = [n.get("name") for n in _nodes_assigned_to(parsed, name)]
    return {
        "deleted": removed > 0,
        "rooms_removed": removed,
        "floor_id": floor_id,
        "orphaned_nodes": orphaned,
    }


# ──────────────────────────────────────────────── geometry-aware operations
#
# Everything below is `geometry.py` maths bound to the config shape. The split
# is deliberate: the maths is pure and exhaustively unit-tested, while these
# wrappers only locate the right room dict and write flow-style sequences back
# so an edited polygon still renders as `[[0, 0], [4, 0]]` next to its
# hand-authored neighbours.


def _find_room(parsed: Any, name: str) -> tuple[Any, Any]:
    """Return (floor, room) for a room name, or raise KeyError."""
    target = (name or "").strip()
    for fl in parsed.get("floors") or []:
        for room in fl.get("rooms") or []:
            if room.get("name") == target:
                return fl, room
    raise KeyError(f"no room named {target!r}")


def _write_points(room: Any, points: list) -> None:
    room["points"] = yaml_io.flow_seq(
        [yaml_io.flow_seq([round(float(x), 6), round(float(y), 6)]) for x, y in points]
    )


def geometry_report(parsed: Any, floor_id: Optional[str] = None) -> list[dict]:
    """Per-room area / perimeter / centroid / bbox, plus node containment.

    `node_outside` is the payoff: it answers "is this node actually in the
    room it claims?" — the mistake that produces a config which validates,
    starts, and localises to the wrong place.
    """
    out: list[dict] = []
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        if floor_id and fid != floor_id:
            continue
        for room in fl.get("rooms") or []:
            name = room.get("name")
            row: dict = {
                "floor_id": fid,
                "room_name": name,
                "point_count": len(room.get("points") or []),
            }
            try:
                pts = geometry.normalize_polygon(room.get("points"))
            except geometry.GeometryError as exc:
                row.update(
                    {
                        "area": None,
                        "perimeter": None,
                        "centroid": None,
                        "bbox": None,
                        "error": str(exc),
                    }
                )
                out.append(row)
                continue
            cx, cy = geometry.centroid(pts)
            inside: list[str] = []
            outside: list[str] = []
            for node in _nodes_assigned_to(parsed, name):
                point = list(node.get("point") or [])
                if len(point) < 2:
                    continue
                try:
                    hit = geometry.contains_point(pts, point[0], point[1])
                except geometry.GeometryError:
                    continue
                (inside if hit else outside).append(node.get("name"))
            row.update(
                {
                    "area": round(geometry.area(pts), 4),
                    "perimeter": round(geometry.perimeter(pts), 4),
                    "centroid": [round(cx, 4), round(cy, 4)],
                    "bbox": {k: round(v, 4) for k, v in geometry.bbox(pts).items()},
                    "nodes_inside": inside,
                    "nodes_outside": outside,
                }
            )
            out.append(row)
    return out


def locate_point(parsed: Any, x: float, y: float, floor_id: Optional[str] = None) -> list[dict]:
    """Which room polygon(s) contain (x, y)?

    Returns one row per hit with the distance from the room centroid, so a
    caller can pick the most plausible when a point lands in an overlap.
    An empty list means "nowhere" — useful before `nodes set-point`.
    """
    import math

    hits: list[dict] = []
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        if floor_id and fid != floor_id:
            continue
        for room in fl.get("rooms") or []:
            try:
                pts = geometry.normalize_polygon(room.get("points"))
            except geometry.GeometryError:
                continue
            if not geometry.contains_point(pts, x, y):
                continue
            cx, cy = geometry.centroid(pts)
            hits.append(
                {
                    "floor_id": fid,
                    "room_name": room.get("name"),
                    "centroid": [round(cx, 4), round(cy, 4)],
                    "distance_from_centroid": round(math.dist((float(x), float(y)), (cx, cy)), 4),
                    "on_boundary": geometry.on_boundary(pts, x, y),
                }
            )
    return sorted(hits, key=lambda h: h["distance_from_centroid"])


def find_overlaps(parsed: Any, floor_id: Optional[str] = None) -> list[dict]:
    """Pairs of rooms on the same floor whose polygons share area.

    Only same-floor pairs are compared: two floors reusing the same x/y
    footprint is normal (that is what a building is), while two rooms
    overlapping on one floor makes localisation ambiguous.
    """
    out: list[dict] = []
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        if floor_id and fid != floor_id:
            continue
        rooms_list = [r for r in (fl.get("rooms") or [])]
        for i in range(len(rooms_list)):
            for j in range(i + 1, len(rooms_list)):
                try:
                    a = geometry.normalize_polygon(rooms_list[i].get("points"))
                    b = geometry.normalize_polygon(rooms_list[j].get("points"))
                except geometry.GeometryError:
                    continue
                if geometry.overlaps(a, b):
                    out.append(
                        {
                            "floor_id": fid,
                            "room_a": rooms_list[i].get("name"),
                            "room_b": rooms_list[j].get("name"),
                        }
                    )
    return out


def set_points(parsed: Any, name: str, points: list) -> dict:
    """Replace a room's polygon wholesale. Returns {found, before, after}."""
    pts = geometry.normalize_polygon(points)
    fl, room = _find_room(parsed, name)
    before = [list(p) for p in (room.get("points") or [])]
    _write_points(room, pts)
    return {
        "found": True,
        "floor_id": fl.get("id"),
        "room_name": room.get("name"),
        "before": before,
        "after": [list(p) for p in pts],
        "point_count": len(pts),
    }


def move_room(parsed: Any, name: str, dx: float, dy: float) -> dict:
    """Translate a room polygon by (dx, dy) — the whole room, same shape."""
    fl, room = _find_room(parsed, name)
    pts = geometry.translate(room.get("points"), dx, dy)
    before = [list(p) for p in (room.get("points") or [])]
    _write_points(room, pts)
    return {
        "found": True,
        "floor_id": fl.get("id"),
        "room_name": room.get("name"),
        "dx": float(dx),
        "dy": float(dy),
        "before": before,
        "after": [list(p) for p in pts],
    }


def scale_room(parsed: Any, name: str, factor: float, *, about_origin: bool = False) -> dict:
    """Scale a room polygon about its centroid (or about (0, 0))."""
    fl, room = _find_room(parsed, name)
    origin = (0.0, 0.0) if about_origin else None
    pts = geometry.scale(room.get("points"), factor, origin=origin)
    before = [list(p) for p in (room.get("points") or [])]
    _write_points(room, pts)
    return {
        "found": True,
        "floor_id": fl.get("id"),
        "room_name": room.get("name"),
        "factor": float(factor),
        "about": "origin" if about_origin else "centroid",
        "before": before,
        "after": [list(p) for p in pts],
        "area_before": round(geometry.area(before), 4),
        "area_after": round(geometry.area(pts), 4),
    }


def set_color(parsed: Any, name: str, color: Optional[str]) -> dict:
    """Set (or, with color=None, remove) a room's `color:`."""
    fl, room = _find_room(parsed, name)
    before = room.get("color")
    if color is None:
        room.pop("color", None)
    else:
        room["color"] = color
    return {
        "found": True,
        "floor_id": fl.get("id"),
        "room_name": room.get("name"),
        "before": before,
        "after": color,
    }


def centroid_of(parsed: Any, name: str) -> tuple[float, float]:
    """Centroid of a named room — the natural default spot to put a node."""
    _, room = _find_room(parsed, name)
    return geometry.centroid(room.get("points"))
