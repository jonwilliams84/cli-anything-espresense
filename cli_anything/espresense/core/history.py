"""Device-history wrappers + movement analytics.

`get_history` is the thin transport wrapper; `trail` is the pure aggregator
the CLI's `history trail` command runs over the fetched rows. Keeping the
aggregation pure (like `telemetry.aggregate_*`) means it is unit-testable
without a companion.
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient

# Sentinel so a first row whose room is legitimately None still opens a
# segment instead of being merged into an impossible previous one.
_UNSET = object()


def get_history(
    client: CompanionClient, device_id: str, start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    resp = companion_api.get_device_history(client, device_id, start=start, end=end)
    if isinstance(resp, dict):
        return resp.get("history") or []
    return resp if isinstance(resp, list) else []


def _row_field(row: dict, *names: str) -> Any:
    """First present, non-None field among the accepted spellings.

    History rows have shipped both `roomName`/`unixTs` (companion) and
    `room`/`ts` (older/alternate) spellings — `telemetry.whereis` already
    accepts both, and so must the analytics built on the same rows.
    """
    for n in names:
        v = row.get(n)
        if v is not None:
            return v
    return None


def trail(rows: list[dict]) -> dict:
    """Summarise a device's movement from its history rows.

    Takes the rows in the order the companion returns them (oldest first) and
    folds them into consecutive room segments: a new segment starts whenever
    the room changes, so a device that ping-pongs between two rooms produces
    one segment per visit, not one per room. Rows without a room attribution
    still count (as a `room: None` segment) so point counts stay honest, but
    they are excluded from `rooms_visited` — an unknown room is not a visit.

    No I/O, no schema assumptions beyond the field spellings above.
    """
    segments: list[dict] = []
    rooms_visited: list = []
    last_room: Any = _UNSET
    for row in rows:
        if not isinstance(row, dict):
            continue
        room = _row_field(row, "roomName", "room")
        ts = _row_field(row, "unixTs", "ts", "timestamp")
        if room is not None and room not in rooms_visited:
            rooms_visited.append(room)
        if last_room is _UNSET or room != last_room:
            segments.append({"room": room, "points": 1, "first_seen": ts, "last_seen": ts})
            last_room = room
        else:
            seg = segments[-1]
            seg["points"] += 1
            seg["last_seen"] = ts
    return {
        "points": sum(s["points"] for s in segments),
        "first_seen": segments[0]["first_seen"] if segments else None,
        "last_seen": segments[-1]["last_seen"] if segments else None,
        "rooms_visited": rooms_visited,
        "segments": segments,
    }
