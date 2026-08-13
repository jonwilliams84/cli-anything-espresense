"""Pure 2D polygon / 3D bounds maths for the espresense floor plan.

Everything in an espresense `config.yaml` that matters for localisation is
geometry: a floor has `bounds` ([[x0,y0,z0],[x1,y1,z1]]), a room is a closed
polygon of `points` ([[x,y], ...]), and a node sits at a `point` ([x,y,z])
that is supposed to be *inside* the room it names.

Until now the harness could edit those numbers (`rooms add`, `nodes set-point`)
but never reason about them, so the two most common floor-plan mistakes were
invisible:

  * a node whose `point:` is nowhere near the polygon its `room:` names — the
    config validates, the companion starts, and the device just localises to
    the wrong place;
  * two room polygons overlapping, which makes "which room is this?"
    ambiguous no matter how good the calibration is.

This module is deliberately pure: no I/O, no YAML, no config shape. It takes
lists of numbers and returns numbers or booleans, so it is cheap to test
exhaustively and safe to call from `validate.check()` (which must stay
read-only). The config-shaped wrappers live in `rooms.py` / `floors.py`.

Conventions
-----------
* A polygon is an implicitly-closed sequence of >=3 [x, y] vertices. A
  duplicated last==first vertex is tolerated and ignored.
* `contains_point` counts the boundary as inside by default: a node placed
  exactly on a wall is a modelling choice, not an error.
* `overlaps` is a *heuristic* for warning purposes — see its docstring. It is
  built to never fire for the edge-sharing rooms that every real floor plan
  is made of.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence

Point = tuple[float, float]

# Coordinates within this distance are treated as the same place. Room
# polygons are hand-authored in metres, so 1e-9 is far below anything a human
# types while still absorbing float round-off from scale/translate.
EPS = 1e-9


class GeometryError(ValueError):
    """Raised when a polygon or bounds value is not usable geometry."""


def _number(v: Any) -> float:
    # bool is an int subclass; a coordinate of `true` is a config bug, not a 1.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise GeometryError(f"coordinate must be a number, got {v!r}")
    return float(v)


def normalize_polygon(points: Any) -> list[Point]:
    """Coerce a config `points:` value into [(x, y), ...].

    Accepts anything list-like (including ruamel's CommentedSeq) and drops a
    trailing vertex that repeats the first, since both conventions appear in
    hand-authored configs.
    """
    if points is None or isinstance(points, (str, bytes)) or not isinstance(points, Iterable):
        raise GeometryError(f"polygon must be a sequence of [x, y] pairs, got {points!r}")
    out: list[Point] = []
    for pt in points:
        if isinstance(pt, (str, bytes)) or not isinstance(pt, Iterable):
            raise GeometryError(f"polygon vertex must be [x, y], got {pt!r}")
        coords = list(pt)
        if len(coords) != 2:
            raise GeometryError(f"polygon vertex must have exactly 2 numbers, got {coords!r}")
        out.append((_number(coords[0]), _number(coords[1])))
    if len(out) > 1 and _same(out[0], out[-1]):
        out.pop()
    if len(out) < 3:
        raise GeometryError(f"polygon needs at least 3 distinct vertices, got {len(out)}")
    return out


def _same(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) <= EPS and abs(a[1] - b[1]) <= EPS


def signed_area(points: Any) -> float:
    """Shoelace area; positive when the vertices wind counter-clockwise."""
    pts = normalize_polygon(points)
    total = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def area(points: Any) -> float:
    """Unsigned polygon area (winding-order independent)."""
    return abs(signed_area(points))


def perimeter(points: Any) -> float:
    pts = normalize_polygon(points)
    return sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def centroid(points: Any) -> Point:
    """Area centroid, falling back to the vertex mean for zero-area polygons.

    The fallback matters: a degenerate polygon (all points collinear, which
    `validate` flags but does not forbid) has no area centroid, and callers
    like `rooms scale` still need *some* stable origin rather than a
    ZeroDivisionError.
    """
    pts = normalize_polygon(points)
    a = signed_area(pts)
    if abs(a) <= EPS:
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    cx = cy = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * a), cy / (6.0 * a))


def bbox(points: Any) -> dict:
    """Axis-aligned bounding box as a dict (JSON-friendly, --json prints it)."""
    pts = normalize_polygon(points)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > EPS:
        return False
    return (
        min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
        and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
    )


def on_boundary(points: Any, x: float, y: float) -> bool:
    """True when (x, y) lies on any edge of the polygon."""
    pts = normalize_polygon(points)
    p = (_number(x), _number(y))
    return any(_on_segment(p, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def contains_point(points: Any, x: float, y: float, *, include_boundary: bool = True) -> bool:
    """Ray-casting point-in-polygon test.

    `include_boundary` decides what a point sitting exactly on a wall means.
    It defaults to True for the operator-facing question ("is this node in
    this room?") and is set False by `overlaps`, where boundary contact is
    precisely the case that must NOT count.
    """
    pts = normalize_polygon(points)
    # _number, not float(): a coordinate of "1.5" or True is a config bug, and
    # raising GeometryError lets callers skip the row instead of crashing on a
    # ValueError from deep inside the maths.
    px, py = _number(x), _number(y)
    if on_boundary(pts, px, py):
        return include_boundary
    inside = False
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        if (y1 > py) != (y2 > py):
            x_at = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < x_at:
                inside = not inside
    return inside


def translate(points: Any, dx: float, dy: float) -> list[Point]:
    return [(p[0] + float(dx), p[1] + float(dy)) for p in normalize_polygon(points)]


def scale(points: Any, factor: float, origin: Optional[Sequence[float]] = None) -> list[Point]:
    """Scale about `origin` (default: the polygon's own centroid).

    Scaling about the centroid keeps the room where it is and only changes its
    size, which is what "this room is drawn 10% too small" needs. Passing
    origin=(0, 0) gives the raw about-the-origin transform instead.
    """
    f = float(factor)
    if f == 0:
        raise GeometryError("scale factor must be non-zero")
    pts = normalize_polygon(points)
    ox, oy = (float(origin[0]), float(origin[1])) if origin is not None else centroid(pts)
    return [(ox + (p[0] - ox) * f, oy + (p[1] - oy) * f) for p in pts]


def _orient(a: Point, b: Point, c: Point) -> int:
    val = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if val > EPS:
        return 1
    if val < -EPS:
        return -1
    return 0


def segments_cross(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """True when segments a and b *properly* cross (not merely touch).

    Collinear overlap and shared endpoints return False, because adjacent
    rooms in a real floor plan share whole edges and vertices by design.
    """
    d1 = _orient(a1, a2, b1)
    d2 = _orient(a1, a2, b2)
    d3 = _orient(b1, b2, a1)
    d4 = _orient(b1, b2, a2)
    return d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0 and d1 != d2 and d3 != d4


def bboxes_overlap(a: Any, b: Any) -> bool:
    """Strict bounding-box overlap: touching boxes do not count."""
    ba, bb = bbox(a), bbox(b)
    return (
        ba["min_x"] < bb["max_x"] - EPS
        and bb["min_x"] < ba["max_x"] - EPS
        and ba["min_y"] < bb["max_y"] - EPS
        and bb["min_y"] < ba["max_y"] - EPS
    )


def _probes(points: list[Point]) -> list[Point]:
    """Vertices plus edge midpoints — the sample set `overlaps` tests."""
    mids = [
        (
            (points[i][0] + points[(i + 1) % len(points)][0]) / 2.0,
            (points[i][1] + points[(i + 1) % len(points)][1]) / 2.0,
        )
        for i in range(len(points))
    ]
    return [*points, *mids]


def overlaps(a: Any, b: Any) -> bool:
    """Heuristic "do these two rooms share floor area?" test.

    Exact polygon clipping is overkill for a warning, so this combines three
    cheap signals behind a strict bounding-box gate:

      1. a *probe* of one polygon strictly inside the other. Probes are the
         vertices plus every edge midpoint: vertices alone miss two rectangles
         that half-overlap on the same y range, where the overlap is real but
         every corner lands exactly on the other outline.
      2. `a`'s centroid strictly inside `b` — which is what catches two
         *identical* polygons, where every probe lands exactly on the other
         outline and signal 1 stays silent. Only one direction is tested: a
         polygon genuinely contained in another always has probes strictly
         inside it (signal 1), so the mirrored centroid test would add no
         detection — only the risk of a false positive for a C-shaped room
         whose centroid sits in its own hollow.
      3. a properly crossing pair of edges (crossed/rotated rooms whose
         vertices all fall outside each other).

    Deliberately conservative about touching: rooms that share an edge or a
    corner — i.e. every neighbouring pair on a real floor plan — return False,
    because a shared wall puts every probe exactly *on* the other boundary and
    `include_boundary=False` rejects it.
    """
    pa, pb = normalize_polygon(a), normalize_polygon(b)
    if not bboxes_overlap(pa, pb):
        return False
    if any(contains_point(pb, x, y, include_boundary=False) for x, y in _probes(pa)):
        return True
    if any(contains_point(pa, x, y, include_boundary=False) for x, y in _probes(pb)):
        return True
    if contains_point(pb, *centroid(pa), include_boundary=False):
        return True
    for i in range(len(pa)):
        a1, a2 = pa[i], pa[(i + 1) % len(pa)]
        for j in range(len(pb)):
            b1, b2 = pb[j], pb[(j + 1) % len(pb)]
            if segments_cross(a1, a2, b1, b2):
                return True
    return False


# ──────────────────────────────────────────────── floor bounds (3D)


def normalize_bounds(bounds: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Coerce a floor `bounds:` value into ((x0,y0,z0), (x1,y1,z1)), sorted.

    Per-axis sorting means a config that writes the corners in the other
    order still describes the same box instead of an empty one.
    """
    if bounds is None or isinstance(bounds, (str, bytes)) or not isinstance(bounds, Iterable):
        raise GeometryError(f"bounds must be [[x0,y0,z0],[x1,y1,z1]], got {bounds!r}")
    corners = list(bounds)
    if len(corners) != 2:
        raise GeometryError(f"bounds needs exactly 2 corners, got {len(corners)}")
    vals: list[tuple[float, float, float]] = []
    for corner in corners:
        if isinstance(corner, (str, bytes)) or not isinstance(corner, Iterable):
            raise GeometryError(f"bounds corner must be [x, y, z], got {corner!r}")
        coords = list(corner)
        if len(coords) != 3:
            raise GeometryError(f"bounds corner must have 3 numbers, got {coords!r}")
        vals.append((_number(coords[0]), _number(coords[1]), _number(coords[2])))
    lo = tuple(min(vals[0][i], vals[1][i]) for i in range(3))
    hi = tuple(max(vals[0][i], vals[1][i]) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def point_in_bounds(bounds: Any, x: float, y: float, z: Optional[float] = None) -> bool:
    """Is [x, y(, z)] inside the floor box? Boundary counts as inside."""
    lo, hi = normalize_bounds(bounds)
    if not (lo[0] - EPS <= _number(x) <= hi[0] + EPS):
        return False
    if not (lo[1] - EPS <= _number(y) <= hi[1] + EPS):
        return False
    if z is not None and not (lo[2] - EPS <= _number(z) <= hi[2] + EPS):
        return False
    return True


def polygon_in_bounds(points: Any, bounds: Any) -> bool:
    """Every vertex of the room polygon inside the floor's x/y extent."""
    return all(point_in_bounds(bounds, x, y) for x, y in normalize_polygon(points))


def bounds_from_polygons(
    polygons: Iterable[Any],
    *,
    margin: float = 0.0,
    z_min: float = 0.0,
    z_max: float = 3.0,
) -> list[list[float]]:
    """Smallest bounds containing every polygon, padded by `margin`.

    This is what `floors fit-bounds` uses after rooms are added or moved:
    ESPresense needs a floor box big enough to hold its rooms, and computing
    it beats asking an operator to re-derive two corners by hand.
    """
    boxes = [bbox(p) for p in polygons]
    if not boxes:
        raise GeometryError("cannot derive bounds: no room polygons")
    m = float(margin)
    lo_z, hi_z = sorted((float(z_min), float(z_max)))
    return [
        [min(b["min_x"] for b in boxes) - m, min(b["min_y"] for b in boxes) - m, lo_z],
        [max(b["max_x"] for b in boxes) + m, max(b["max_y"] for b in boxes) + m, hi_z],
    ]
