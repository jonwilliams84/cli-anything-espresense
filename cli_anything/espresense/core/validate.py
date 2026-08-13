"""Consistency checks for an espresense config.yaml.

`rooms.py` exists to *repair* one specific failure mode — a node's `room:`
string drifting out of sync with the floor polygon it is supposed to name
(usually via a rename, a typo, or a trailing space that YAML happily keeps).
Nothing in the harness *detected* that drift, so the operator had to already
know something was wrong before reaching for the fix.

`check()` walks a parsed config and returns structured findings. It is pure:
no network, no kubectl, no mutation — which makes it safe to run against a
local file, in CI, or as a pre-flight before a push.

Findings are split into:
  errors   — the companion will misbehave (dangling room refs, duplicate ids)
  warnings — probably not what you meant (room with no node, 2-point polygon)

Every finding is a dict with a stable `code` so an agent can branch on it
without parsing prose.

A second class of drift is *geometric* rather than textual: the `room:` string
matches a polygon, so every string-level check passes, but the node's `point:`
is nowhere inside that polygon — or two polygons on one floor overlap, or a
room sticks out past its floor `bounds`. Those configs load, start, and
localise to the wrong place, which is far harder to debug than a hard error.
`geometry.py` supplies the maths; the checks here stay pure and warning-level,
because unusual-but-intentional floor plans exist and this must not block a
push on taste.
"""

from __future__ import annotations

from typing import Any

from cli_anything.espresense.core import geometry

# Stable finding codes. Keep these strings frozen — callers match on them.
DANGLING_ROOM_REF = "dangling_room_ref"
ROOM_REF_WHITESPACE = "room_ref_whitespace"
DUPLICATE_ROOM_NAME = "duplicate_room_name"
DUPLICATE_NODE_NAME = "duplicate_node_name"
DUPLICATE_FLOOR_ID = "duplicate_floor_id"
NODE_MISSING_ROOM = "node_missing_room"
NODE_MISSING_NAME = "node_missing_name"
BAD_NODE_POINT = "bad_node_point"
DEGENERATE_POLYGON = "degenerate_polygon"
ROOM_WITHOUT_NODE = "room_without_node"
NO_FLOORS = "no_floors"
NO_NODES = "no_nodes"
# Geometry-level findings (added later; same stability guarantee).
DANGLING_FLOOR_REF = "dangling_floor_ref"
BAD_FLOOR_BOUNDS = "bad_floor_bounds"
NODE_POINT_OUTSIDE_ROOM = "node_point_outside_room"
NODE_POINT_OUTSIDE_BOUNDS = "node_point_outside_bounds"
ROOM_OUTSIDE_FLOOR_BOUNDS = "room_outside_floor_bounds"
ROOM_OVERLAP = "room_overlap"


def _finding(level: str, code: str, message: str, **ctx) -> dict:
    out = {"level": level, "code": code, "message": message}
    out.update({k: v for k, v in ctx.items() if v is not None})
    return out


def _is_number(v: Any) -> bool:
    # bool is an int subclass; a coordinate of `true` is a config bug, not a 1.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _room_index(parsed: Any) -> tuple[dict[str, list[str]], list[dict]]:
    """Map room-name -> [floor_id...] and collect duplicate/degenerate findings."""
    index: dict[str, list[str]] = {}
    findings: list[dict] = []
    seen_floor_ids: dict[str, int] = {}
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        if fid is not None:
            seen_floor_ids[fid] = seen_floor_ids.get(fid, 0) + 1
        for room in fl.get("rooms") or []:
            name = room.get("name")
            if name is None:
                continue
            index.setdefault(name, []).append(fid)
            points = room.get("points") or []
            if len(points) < 3:
                findings.append(
                    _finding(
                        "warning",
                        DEGENERATE_POLYGON,
                        f"room {name!r} has {len(points)} polygon point(s); "
                        "a room needs at least 3 to enclose an area",
                        room=name,
                        floor_id=fid,
                        point_count=len(points),
                    )
                )
    for name, floors in index.items():
        if len(floors) > 1:
            findings.append(
                _finding(
                    "error",
                    DUPLICATE_ROOM_NAME,
                    f"room name {name!r} is used {len(floors)} times "
                    f"(floors: {', '.join(str(f) for f in floors)}); "
                    "node `room:` references become ambiguous",
                    room=name,
                    floor_ids=[str(f) for f in floors],
                )
            )
    for fid, count in seen_floor_ids.items():
        if count > 1:
            findings.append(
                _finding(
                    "error",
                    DUPLICATE_FLOOR_ID,
                    f"floor id {fid!r} is declared {count} times",
                    floor_id=fid,
                )
            )
    return index, findings


def _polygon_of(parsed: Any) -> dict[str, dict]:
    """room name -> {floor_id, polygon|None}. Invalid polygons map to None.

    A polygon that will not normalize is already reported by
    DEGENERATE_POLYGON / caught by the config author; the geometry checks just
    skip it rather than emitting a second finding for the same defect.
    """
    out: dict[str, dict] = {}
    for fl in parsed.get("floors") or []:
        for room in fl.get("rooms") or []:
            name = room.get("name")
            if name is None or name in out:
                continue
            try:
                poly = geometry.normalize_polygon(room.get("points"))
            except geometry.GeometryError:
                poly = None
            out[name] = {"floor_id": fl.get("id"), "polygon": poly}
    return out


def _node_point(node: Any) -> list | None:
    """A node's point as 3 numbers, or None when absent/malformed."""
    point = node.get("point")
    if point is None:
        return None
    coords = list(point)
    if len(coords) != 3 or not all(_is_number(c) for c in coords):
        return None  # already reported as BAD_NODE_POINT
    return [float(c) for c in coords]


def _geometry_findings(parsed: Any) -> list[dict]:
    """Geometric consistency checks. Pure; every finding is a warning except
    structurally-broken references (`floors:` refs, unparseable `bounds:`)."""
    findings: list[dict] = []
    floors = parsed.get("floors") or []
    floor_ids = {fl.get("id") for fl in floors if fl.get("id") is not None}
    polygons = _polygon_of(parsed)

    # 1. node `floors:` entries that name no declared floor
    for i, node in enumerate(parsed.get("nodes") or []):
        label = node.get("name") or f"<nodes[{i}]>"
        for ref in node.get("floors") or []:
            if ref not in floor_ids:
                findings.append(
                    _finding(
                        "error",
                        DANGLING_FLOOR_REF,
                        f"node {label!r} lists floor {ref!r}, which no floor declares "
                        "(`floors retag` rewrites both halves together)",
                        node=label,
                        floor_id=ref,
                    )
                )

    # 2. node `point:` outside the polygon its `room:` names
    for i, node in enumerate(parsed.get("nodes") or []):
        label = node.get("name") or f"<nodes[{i}]>"
        room_name = node.get("room")
        room_name = room_name.strip() if isinstance(room_name, str) else room_name
        entry = polygons.get(room_name)
        point = _node_point(node)
        if not entry or entry["polygon"] is None or point is None:
            continue
        if not geometry.contains_point(entry["polygon"], point[0], point[1]):
            findings.append(
                _finding(
                    "warning",
                    NODE_POINT_OUTSIDE_ROOM,
                    f"node {label!r} sits at [{point[0]}, {point[1]}] which is outside "
                    f"room {room_name!r} — the config is valid but the node will be "
                    "drawn in the wrong place",
                    node=label,
                    room=room_name,
                    point=point,
                )
            )

    # 3/4. rooms and nodes escaping their floor `bounds:`
    for fl in floors:
        fid = fl.get("id")
        raw_bounds = fl.get("bounds")
        if raw_bounds is None:
            continue
        try:
            bounds = geometry.normalize_bounds(raw_bounds)
        except geometry.GeometryError as exc:
            findings.append(
                _finding("error", BAD_FLOOR_BOUNDS, f"floor {fid!r} `bounds:` {exc}", floor_id=fid)
            )
            continue
        room_names = []
        for room in fl.get("rooms") or []:
            name = room.get("name")
            room_names.append(name)
            entry = polygons.get(name)
            if not entry or entry["polygon"] is None:
                continue
            if not geometry.polygon_in_bounds(entry["polygon"], bounds):
                findings.append(
                    _finding(
                        "warning",
                        ROOM_OUTSIDE_FLOOR_BOUNDS,
                        f"room {name!r} extends past floor {fid!r} bounds "
                        "(`floors fit-bounds` recomputes them from the rooms)",
                        room=name,
                        floor_id=fid,
                    )
                )
        for i, node in enumerate(parsed.get("nodes") or []):
            label = node.get("name") or f"<nodes[{i}]>"
            refs = node.get("floors") or []
            room_ref = node.get("room")
            room_ref = room_ref.strip() if isinstance(room_ref, str) else room_ref
            on_floor = fid in refs or (not refs and room_ref in room_names)
            point = _node_point(node)
            if not on_floor or point is None:
                continue
            if not geometry.point_in_bounds(bounds, point[0], point[1], point[2]):
                findings.append(
                    _finding(
                        "warning",
                        NODE_POINT_OUTSIDE_BOUNDS,
                        f"node {label!r} at {point} is outside floor {fid!r} bounds",
                        node=label,
                        floor_id=fid,
                        point=point,
                    )
                )

    # 5. overlapping rooms on one floor make "which room?" ambiguous
    for fl in floors:
        fid = fl.get("id")
        rooms_list = fl.get("rooms") or []
        polys = []
        for room in rooms_list:
            try:
                polys.append((room.get("name"), geometry.normalize_polygon(room.get("points"))))
            except geometry.GeometryError:
                continue
        for a in range(len(polys)):
            for b in range(a + 1, len(polys)):
                if geometry.overlaps(polys[a][1], polys[b][1]):
                    findings.append(
                        _finding(
                            "warning",
                            ROOM_OVERLAP,
                            f"rooms {polys[a][0]!r} and {polys[b][0]!r} overlap on floor "
                            f"{fid!r}; a device in the shared area is ambiguous",
                            floor_id=fid,
                            room=polys[a][0],
                            other_room=polys[b][0],
                        )
                    )
    return findings


def check(parsed: Any) -> dict:
    """Validate a parsed config.yaml.

    Returns::

        {
          "ok": bool,                 # no errors (warnings do not clear this)
          "errors": [finding, ...],
          "warnings": [finding, ...],
          "counts": {"floors":n, "rooms":n, "nodes":n, "errors":n, "warnings":n},
        }
    """
    findings: list[dict] = []
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "errors": [_finding("error", "not_a_mapping", "config root is not a YAML mapping")],
            "warnings": [],
            "counts": {"floors": 0, "rooms": 0, "nodes": 0, "errors": 1, "warnings": 0},
        }

    floors = parsed.get("floors") or []
    nodes = parsed.get("nodes") or []

    if not floors:
        findings.append(
            _finding("error", NO_FLOORS, "config declares no `floors:` — nothing to locate against")
        )
    if not nodes:
        findings.append(
            _finding("warning", NO_NODES, "config declares no `nodes:` — no receivers configured")
        )

    room_index, room_findings = _room_index(parsed)
    findings.extend(room_findings)

    seen_node_names: dict[str, int] = {}
    rooms_with_nodes: set[str] = set()

    for i, node in enumerate(nodes):
        name = node.get("name")
        label = name if name is not None else f"<nodes[{i}]>"
        if name is None or (isinstance(name, str) and not name.strip()):
            findings.append(
                _finding("error", NODE_MISSING_NAME, f"nodes[{i}] has no `name:`", index=i)
            )
        else:
            seen_node_names[name] = seen_node_names.get(name, 0) + 1

        raw_room = node.get("room")
        if raw_room is None or (isinstance(raw_room, str) and not raw_room.strip()):
            findings.append(
                _finding(
                    "warning",
                    NODE_MISSING_ROOM,
                    f"node {label!r} has no `room:` — it will not be tied to a polygon",
                    node=label,
                )
            )
        elif isinstance(raw_room, str):
            stripped = raw_room.strip()
            if stripped != raw_room:
                findings.append(
                    _finding(
                        "error",
                        ROOM_REF_WHITESPACE,
                        f"node {label!r} `room:` is {raw_room!r} — the surrounding "
                        "whitespace stops it matching the polygon. `rooms rename` "
                        "strips these as a side effect.",
                        node=label,
                        room=stripped,
                    )
                )
            if stripped not in room_index:
                findings.append(
                    _finding(
                        "error",
                        DANGLING_ROOM_REF,
                        f"node {label!r} points at room {stripped!r}, which no floor declares",
                        node=label,
                        room=stripped,
                    )
                )
            else:
                rooms_with_nodes.add(stripped)

        point = node.get("point")
        if point is not None:
            coords = list(point)
            if len(coords) != 3 or not all(_is_number(c) for c in coords):
                findings.append(
                    _finding(
                        "error",
                        BAD_NODE_POINT,
                        f"node {label!r} `point:` must be 3 numbers [x, y, z], got {coords!r}",
                        node=label,
                    )
                )

    for name, count in seen_node_names.items():
        if count > 1:
            findings.append(
                _finding(
                    "error",
                    DUPLICATE_NODE_NAME,
                    f"node name {name!r} is declared {count} times",
                    node=name,
                )
            )

    for room_name in room_index:
        if room_name not in rooms_with_nodes:
            findings.append(
                _finding(
                    "warning",
                    ROOM_WITHOUT_NODE,
                    f"room {room_name!r} has no node assigned to it",
                    room=room_name,
                )
            )

    findings.extend(_geometry_findings(parsed))

    errors = [f for f in findings if f["level"] == "error"]
    warnings = [f for f in findings if f["level"] == "warning"]
    room_count = sum(len(fl.get("rooms") or []) for fl in floors)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "floors": len(floors),
            "rooms": room_count,
            "nodes": len(nodes),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }
