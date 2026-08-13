"""Structured edits to the `floors:` block of an espresense config.yaml.

`config_yaml.list_floors` / `find_floor` could already *read* floors, and
`rooms.py` could add and delete the polygons inside them — but the floors
themselves were immutable: there was no way to create a floor before adding
rooms to it, fix a floor's display name, correct its `bounds`, or retire one.
An operator adding an upstairs had to hand-edit YAML, which is exactly the
step this harness exists to remove.

Two things here need the same care `rooms.rename` takes:

  * `retag` (changing a floor's `id`) must also rewrite every node's
    `floors:` list, otherwise the id change silently orphans the nodes that
    referenced it — the floor-level twin of the dangling `room:` bug.
  * `delete_floor` takes the rooms on that floor with it, so it reports the
    rooms it removed and the nodes left pointing at them instead of quietly
    shredding half the localisation model.

`fit_bounds` is the geometry-aware helper: it derives the floor box from the
room polygons that are actually on the floor, so bounds stay correct after
`rooms add` / `rooms move` / `rooms scale` without anyone re-deriving two
corners by hand.
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.core import geometry
from cli_anything.espresense.utils import yaml_io


def _floors(parsed: Any) -> list:
    return parsed.get("floors") or []


def _find(parsed: Any, floor_id: str) -> Any:
    for fl in _floors(parsed):
        if fl.get("id") == floor_id:
            return fl
    raise KeyError(f"no floor with id={floor_id!r}")


def _bounds_seq(bounds: Any) -> Any:
    """Render bounds inline (`[[0, 0, 0], [10, 10, 3]]`) like hand-authored files."""
    lo, hi = geometry.normalize_bounds(bounds)
    return yaml_io.flow_seq([yaml_io.flow_seq(list(lo)), yaml_io.flow_seq(list(hi))])


def add_floor(
    parsed: Any,
    floor_id: str,
    *,
    name: Optional[str] = None,
    bounds: Any = None,
) -> dict:
    """Create an empty floor. Returns {added, id, name, bounds}.

    Refuses a duplicate id for the same reason `validate` calls it an error:
    the companion resolves node `floors:` entries by id, so two floors sharing
    one makes every reference ambiguous.
    """
    if not floor_id or not str(floor_id).strip():
        raise ValueError("floor id must be non-empty")
    floor_id = str(floor_id).strip()
    for fl in _floors(parsed):
        if fl.get("id") == floor_id:
            raise ValueError(f"floor id {floor_id!r} already exists")
    if parsed.get("floors") is None:
        parsed["floors"] = []
    entry: dict = {"id": floor_id}
    if name is not None:
        entry["name"] = str(name)
    if bounds is not None:
        entry["bounds"] = _bounds_seq(bounds)
    entry["rooms"] = []
    parsed["floors"].append(entry)
    return {
        "added": True,
        "id": floor_id,
        "name": entry.get("name"),
        "bounds": [list(c) for c in geometry.normalize_bounds(bounds)] if bounds else None,
    }


def rename_floor(parsed: Any, floor_id: str, name: str) -> dict:
    """Change a floor's human-readable `name:` (its `id` is untouched)."""
    fl = _find(parsed, floor_id)
    before = fl.get("name")
    fl["name"] = str(name)
    return {"found": True, "id": floor_id, "before": before, "after": str(name)}


def retag(parsed: Any, old_id: str, new_id: str) -> dict:
    """Change a floor's `id` AND every node `floors:` entry that used it.

    Returns {id_changed, nodes_repointed, old, new}. Doing both halves in one
    operation is the whole point: a bare id edit leaves nodes referencing a
    floor that no longer exists (validate.DANGLING_FLOOR_REF).
    """
    new_id = str(new_id).strip()
    if not new_id:
        raise ValueError("new floor id must be non-empty")
    if old_id == new_id:
        return {"id_changed": False, "nodes_repointed": 0, "old": old_id, "new": new_id}
    for fl in _floors(parsed):
        if fl.get("id") == new_id:
            raise ValueError(f"floor id {new_id!r} already exists")
    fl = _find(parsed, old_id)
    fl["id"] = new_id
    repointed = 0
    for node in parsed.get("nodes") or []:
        refs = node.get("floors")
        if not refs:
            continue
        for i, ref in enumerate(list(refs)):
            if ref == old_id:
                refs[i] = new_id
                repointed += 1
    return {"id_changed": True, "nodes_repointed": repointed, "old": old_id, "new": new_id}


def set_bounds(parsed: Any, floor_id: str, bounds: Any) -> dict:
    """Set a floor's 3D `bounds:` explicitly. Returns {found, before, after}."""
    fl = _find(parsed, floor_id)
    before = [list(c) for c in fl["bounds"]] if fl.get("bounds") else None
    fl["bounds"] = _bounds_seq(bounds)
    lo, hi = geometry.normalize_bounds(bounds)
    return {"found": True, "id": floor_id, "before": before, "after": [list(lo), list(hi)]}


def _nodes_on_floor(parsed: Any, floor_id: str, room_names: list) -> list[dict]:
    """Nodes tied to a floor, by explicit `floors:` ref or by room membership."""
    out: list[dict] = []
    for node in parsed.get("nodes") or []:
        refs = node.get("floors") or []
        if floor_id in refs:
            out.append(node)
        elif not refs and (node.get("room") or "").strip() in room_names:
            out.append(node)
    return out


def fit_bounds(
    parsed: Any,
    floor_id: str,
    *,
    margin: float = 0.0,
    z_min: Optional[float] = None,
    z_max: Optional[float] = None,
) -> dict:
    """Derive `bounds:` from the floor's room polygons (plus `margin`).

    The z extent is not derivable from 2D polygons, so it is resolved in
    priority order: explicit `z_min`/`z_max` args, then the existing bounds
    (so a fit after moving rooms does not flatten a known ceiling height),
    otherwise a default 0..3 m box *widened* to contain every node on the
    floor — a node mounted at 3.6 m raises the ceiling, a node at 2.4 m does
    not lower it onto itself.
    """
    fl = _find(parsed, floor_id)
    rooms = fl.get("rooms") or []
    polygons = [r.get("points") for r in rooms if r.get("points")]
    if not polygons:
        raise ValueError(f"floor {floor_id!r} has no room polygons to fit bounds to")
    existing = None
    if fl.get("bounds"):
        try:
            existing = geometry.normalize_bounds(fl["bounds"])
        except geometry.GeometryError:
            existing = None
    lo_z = z_min
    hi_z = z_max
    if lo_z is None and existing:
        lo_z = existing[0][2]
    if hi_z is None and existing:
        hi_z = existing[1][2]
    if lo_z is None or hi_z is None:
        zs = []
        room_names = [r.get("name") for r in rooms]
        for node in _nodes_on_floor(parsed, floor_id, room_names):
            point = list(node.get("point") or [])
            if len(point) == 3 and isinstance(point[2], (int, float)):
                zs.append(float(point[2]))
        if lo_z is None:
            lo_z = min([*zs, 0.0])
        if hi_z is None:
            hi_z = max([*zs, 3.0])
    bounds = geometry.bounds_from_polygons(polygons, margin=margin, z_min=lo_z, z_max=hi_z)
    before = [list(c) for c in existing] if existing else None
    fl["bounds"] = _bounds_seq(bounds)
    return {
        "id": floor_id,
        "before": before,
        "after": bounds,
        "margin": float(margin),
        "rooms_considered": len(polygons),
    }


def delete_floor(parsed: Any, floor_id: str) -> dict:
    """Remove a floor and its rooms. Reports the fallout, does not hide it.

    Returns {deleted, id, rooms_removed, room_names, orphaned_nodes,
    nodes_referencing}. Nodes are never rewritten here — see
    `rooms.delete_room` for the same deliberate choice.
    """
    floors = _floors(parsed)
    for i, fl in enumerate(floors):
        if fl.get("id") != floor_id:
            continue
        room_names = [r.get("name") for r in (fl.get("rooms") or [])]
        referencing = [
            n.get("name") for n in parsed.get("nodes") or [] if floor_id in (n.get("floors") or [])
        ]
        orphaned = [
            n.get("name")
            for n in parsed.get("nodes") or []
            if (n.get("room") or "").strip() in [str(r) for r in room_names if r is not None]
        ]
        del floors[i]
        return {
            "deleted": True,
            "id": floor_id,
            "rooms_removed": len(room_names),
            "room_names": room_names,
            "orphaned_nodes": orphaned,
            "nodes_referencing": referencing,
        }
    return {
        "deleted": False,
        "id": floor_id,
        "rooms_removed": 0,
        "room_names": [],
        "orphaned_nodes": [],
        "nodes_referencing": [],
    }
