"""Live presence queries: where-is, distance snapshots, node status, occupancy.

The harness could already *configure* an ESPresense deployment end to end, but
the everyday operational questions of a presence system were only reachable by
hand-rolling `mqtt watch` and scraping raw history rows. This module wraps
those questions into structured answers:

- `whereis`          — last known position of one device (companion history)
- `distance_snapshot` — which nodes see which devices, at what distance
                        (`<prefix>/rooms/+/devices/+` over MQTT)
- `status_snapshot`   — which nodes report online/offline
                        (`<prefix>/rooms/<node>/status`, retained by nodes)
- `telemetry_snapshot` — node health: uptime, free memory, wifi RSSI, firmware
                        version, IP (`<prefix>/rooms/<node>/telemetry`)
- `occupancy`         — which tracked devices are currently in which room

Everything that touches the broker goes through `mqtt.watch`; everything else
in this module is pure functions over the collected records, so aggregation
and filtering are unit-testable without a broker.
"""

from __future__ import annotations

import json
from typing import Optional

from cli_anything.espresense.core import history as history_core
from cli_anything.espresense.core import mqtt as mqtt_core


class TelemetryError(RuntimeError):
    pass


# ── payload / topic parsing ──────────────────────────────────────────────────


def parse_distance_payload(payload: str) -> Optional[float]:
    """Parse an MQTT distance message into metres, or None if it is not one.

    ESPresense nodes have published both shapes over the years: a plain number
    ("3.4") and a JSON object ({"distance": 3.4, ...}). Accept both; ignore
    anything else so one malformed publisher cannot break a snapshot.
    """
    text = (payload or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        val = obj.get("distance")
        # bool is an int subclass — a JSON true is not a distance
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _strip_prefix(topic: str, prefix: str) -> Optional[str]:
    if topic == prefix:
        return ""
    if topic.startswith(prefix + "/"):
        return topic[len(prefix) + 1 :]
    return None


def _parse_distance_topic(topic: str, prefix: str) -> Optional[tuple[str, str]]:
    """Split `<prefix>/rooms/<node>/devices/<device>` into (node, device)."""
    rest = _strip_prefix(topic or "", prefix)
    if rest is None:
        return None
    parts = rest.split("/")
    if len(parts) == 4 and parts[0] == "rooms" and parts[2] == "devices":
        return parts[1], parts[3]
    return None


def _parse_status_topic(topic: str, prefix: str) -> Optional[str]:
    """Extract the node id from `<prefix>/rooms/<node>/status`."""
    rest = _strip_prefix(topic or "", prefix)
    if rest is None:
        return None
    parts = rest.split("/")
    if len(parts) == 3 and parts[0] == "rooms" and parts[2] == "status":
        return parts[1]
    return None


# ── distances ────────────────────────────────────────────────────────────────


def aggregate_distances(
    records: list[dict],
    *,
    prefix: str = "espresense",
    device_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> dict:
    """Aggregate raw `mqtt.watch` records into a per-device/per-node table.

    Each surviving (device, node) pair keeps the most recent distance plus
    sample count and min/max over the collection window. Records that do not
    match the topic shape, the requested device/node, or a parseable payload
    are dropped — one chatty or malformed topic never corrupts the snapshot.
    """
    devices: dict[str, dict[str, dict]] = {}
    messages = 0
    for rec in records:
        parsed = _parse_distance_topic(rec.get("topic", ""), prefix)
        if parsed is None:
            continue
        node, dev = parsed
        if device_id is not None and dev != device_id:
            continue
        if node_id is not None and node != node_id:
            continue
        dist = parse_distance_payload(rec.get("payload", ""))
        if dist is None:
            continue
        messages += 1
        entry = devices.setdefault(dev, {}).setdefault(
            node, {"samples": 0, "min": dist, "max": dist, "distance": dist}
        )
        entry["samples"] += 1
        entry["min"] = min(entry["min"], dist)
        entry["max"] = max(entry["max"], dist)
        entry["distance"] = dist
        if rec.get("ts") is not None:
            entry["last_ts"] = rec["ts"]
    return {"devices": devices, "messages": messages}


def nearest(devices: dict) -> dict:
    """For each device, its nodes ordered by most recent distance (closest first)."""
    out: dict[str, list[dict]] = {}
    for dev, nodes in devices.items():
        rows = [{"node": n, "distance": e["distance"]} for n, e in nodes.items()]
        rows.sort(key=lambda r: r["distance"])
        out[dev] = rows
    return out


def distance_rows(snapshot: dict) -> list[dict]:
    """Flatten a snapshot into table rows, flagging each device's closest node."""
    rows: list[dict] = []
    for dev in sorted(snapshot.get("devices", {})):
        near = None
        ranked = snapshot.get("nearest", {}).get(dev) or nearest(
            {dev: snapshot["devices"][dev]}
        ).get(dev, [])
        if ranked:
            near = ranked[0]["node"]
        for node, entry in sorted(
            snapshot["devices"][dev].items(), key=lambda kv: kv[1]["distance"]
        ):
            rows.append(
                {
                    "device": dev,
                    "node": node,
                    "distance": entry["distance"],
                    "samples": entry["samples"],
                    "min": entry["min"],
                    "max": entry["max"],
                    "nearest": node == near,
                }
            )
    return rows


def distance_snapshot(
    host: str,
    *,
    duration: float = 10.0,
    prefix: str = "espresense",
    device_id: Optional[str] = None,
    node_id: Optional[str] = None,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> dict:
    """Subscribe to `<prefix>/rooms/+/devices/+` for `duration` seconds.

    Returns the aggregated per-device/per-node table plus a `nearest` ranking.
    This is the readable counterpart to the raw `mqtt watch` firehose.
    """
    topic_filter = f"{prefix}/rooms/+/devices/+"
    records = mqtt_core.watch(
        host,
        topic_filter,
        port=port,
        username=username,
        password=password,
        duration=duration,
    )
    snap = aggregate_distances(records, prefix=prefix, device_id=device_id, node_id=node_id)
    snap["nearest"] = nearest(snap["devices"])
    snap.update({"topic_filter": topic_filter, "duration": duration, "prefix": prefix})
    return snap


# ── node status ──────────────────────────────────────────────────────────────


def aggregate_status(records: list[dict], *, prefix: str = "espresense") -> dict:
    """Aggregate retained `<prefix>/rooms/+/status` records into online/offline.

    Nodes publish "online"/"offline" as retained messages, so a short listen
    catches every node even when nothing is actively publishing. Unknown
    payloads are ignored rather than guessed about.
    """
    online: set[str] = set()
    offline: set[str] = set()
    for rec in records:
        node = _parse_status_topic(rec.get("topic", ""), prefix)
        if node is None:
            continue
        payload = (rec.get("payload") or "").strip().lower()
        if payload == "online":
            online.add(node)
        elif payload == "offline":
            offline.add(node)
    return {"online": sorted(online), "offline": sorted(offline)}


def status_snapshot(
    host: str,
    *,
    duration: float = 5.0,
    prefix: str = "espresense",
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> dict:
    """Subscribe to `<prefix>/rooms/+/status` for `duration` seconds."""
    topic_filter = f"{prefix}/rooms/+/status"
    records = mqtt_core.watch(
        host,
        topic_filter,
        port=port,
        username=username,
        password=password,
        duration=duration,
    )
    out = aggregate_status(records, prefix=prefix)
    out.update({"topic_filter": topic_filter, "duration": duration, "prefix": prefix})
    return out


# ── node telemetry ───────────────────────────────────────────────────────────

# Keys ESPresense nodes have shipped in their telemetry payloads, mapped to
# the canonical field names this module reports. Numeric fields get
# worst-case (max uptime / min free mem / min rssi) tracking over the
# window; everything else is reported as last-seen.
_TELEMETRY_ALIASES = {
    "uptime": "uptime",
    "freeMem": "free_mem",
    "free_mem": "free_mem",
    "rssi": "rssi",
    "ip": "ip",
    "model": "model",
    "ver": "version",
    "version": "version",
    "flavor": "flavor",
}
_TELEMETRY_NUMERIC = ("uptime", "free_mem", "rssi")


def parse_telemetry_payload(payload: str) -> Optional[dict]:
    """Parse an MQTT node-telemetry message into a canonical dict, or None.

    Nodes publish a JSON object on `<prefix>/rooms/<node>/telemetry`. Known
    keys are normalised (`freeMem` -> `free_mem`, `ver` -> `version`); unknown
    keys pass through untouched so a newer firmware's extra fields stay
    visible. Numeric aliases are coerced where possible; anything that is not
    a JSON object is rejected so a malformed publisher cannot corrupt a
    snapshot.
    """
    text = (payload or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for k, v in obj.items():
        canon = _TELEMETRY_ALIASES.get(k, k)
        if canon in _TELEMETRY_NUMERIC and isinstance(v, str):
            try:
                v = float(v) if "." in v else int(v)
            except ValueError:
                return None
        out[canon] = v
    return out


def _parse_telemetry_topic(topic: str, prefix: str) -> Optional[str]:
    """Extract the node id from `<prefix>/rooms/<node>/telemetry`."""
    rest = _strip_prefix(topic or "", prefix)
    if rest is None:
        return None
    parts = rest.split("/")
    if len(parts) == 3 and parts[0] == "rooms" and parts[2] == "telemetry":
        return parts[1]
    return None


def aggregate_node_telemetry(
    records: list[dict],
    *,
    prefix: str = "espresense",
    node_id: Optional[str] = None,
) -> dict:
    """Aggregate raw `mqtt.watch` records into a per-node health table.

    Each reporting node keeps its sample count, the most recent full payload
    (`latest`), and worst-case numeric readings over the window: max `uptime`,
    min `free_mem`, min `rssi`. Records with the wrong topic shape, the wrong
    node, or an unparseable payload are dropped — one malformed publisher
    never corrupts the snapshot.
    """
    nodes: dict[str, dict] = {}
    messages = 0
    for rec in records:
        node = _parse_telemetry_topic(rec.get("topic", ""), prefix)
        if node is None:
            continue
        if node_id is not None and node != node_id:
            continue
        parsed = parse_telemetry_payload(rec.get("payload", ""))
        if parsed is None:
            continue
        messages += 1
        entry = nodes.setdefault(node, {"samples": 0, "latest": {}})
        entry["samples"] += 1
        if rec.get("ts") is not None:
            entry["last_ts"] = rec["ts"]
        for key in _TELEMETRY_NUMERIC:
            val = parsed.get(key)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            if key == "uptime":
                cur = entry.get("uptime")
                entry["uptime"] = val if cur is None else max(cur, val)
            else:  # free_mem / rssi: lower is worse
                cur = entry.get(key)
                entry[key] = val if cur is None else min(cur, val)
        entry["latest"] = parsed
    lowest = None
    mems = {n: e.get("free_mem") for n, e in nodes.items() if e.get("free_mem") is not None}
    if mems:
        worst = min(mems, key=lambda n: mems[n])
        lowest = {"node": worst, "free_mem": mems[worst]}
    return {
        "nodes": nodes,
        "messages": messages,
        "nodes_reporting": sorted(nodes),
        "lowest_free_mem": lowest,
    }


def telemetry_rows(snapshot: dict) -> list[dict]:
    """Flatten a node-telemetry snapshot into one row per node (sorted)."""
    rows: list[dict] = []
    for node in sorted(snapshot.get("nodes", {})):
        entry = snapshot["nodes"][node]
        latest = entry.get("latest") or {}
        rows.append(
            {
                "node": node,
                "samples": entry.get("samples", 0),
                "uptime": entry.get("uptime", latest.get("uptime")),
                "free_mem": entry.get("free_mem", latest.get("free_mem")),
                "rssi": entry.get("rssi", latest.get("rssi")),
                "ip": latest.get("ip"),
                "model": latest.get("model"),
                "version": latest.get("version"),
                "last_ts": entry.get("last_ts"),
            }
        )
    return rows


def telemetry_snapshot(
    host: str,
    *,
    duration: float = 5.0,
    prefix: str = "espresense",
    node_id: Optional[str] = None,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> dict:
    """Subscribe to `<prefix>/rooms/+/telemetry` for `duration` seconds.

    Returns the aggregated per-node health table — the readable counterpart
    to scraping the raw `mqtt watch 'espresense/rooms/+/telemetry'` firehose.
    """
    topic_filter = f"{prefix}/rooms/+/telemetry"
    records = mqtt_core.watch(
        host,
        topic_filter,
        port=port,
        username=username,
        password=password,
        duration=duration,
    )
    out = aggregate_node_telemetry(records, prefix=prefix, node_id=node_id)
    out.update({"topic_filter": topic_filter, "duration": duration, "prefix": prefix})
    return out


# ── occupancy ────────────────────────────────────────────────────────────────


def occupancy(rows: list[dict], *, floor: Optional[str] = None) -> dict:
    """Group the companion's device list into a room -> devices mapping.

    `rows` is the output of `devices.list_devices` (room/floor already
    flattened). Devices with no current room land in `unplaced`. The floor
    filter compares against whatever the companion reports for the device
    (floor id or name), case-insensitively.
    """
    rooms: dict[str, list[dict]] = {}
    unplaced: list[dict] = []
    for r in rows:
        if floor is not None and (r.get("floor") or "").strip().lower() != floor.strip().lower():
            continue
        entry = {"id": r.get("id"), "name": r.get("name")}
        room = (r.get("room") or "").strip()
        if room:
            rooms.setdefault(room, []).append(entry)
        else:
            unplaced.append(entry)
    return {"rooms": dict(sorted(rooms.items())), "unplaced": unplaced}


# ── where-is ─────────────────────────────────────────────────────────────────


def whereis(client, device_id: str) -> dict:
    """Last known position of one device, from the companion's history API.

    The companion keeps one row per position report; this takes the most
    recent one and reduces it to the fields an operator asks for. Exits with
    `found: False` (and the CLI then exits 1) when the device has never been
    seen.
    """
    if not device_id or not device_id.strip():
        raise TelemetryError("device id must be non-empty")
    rows = history_core.get_history(client, device_id.strip())
    if not rows:
        return {"device_id": device_id.strip(), "found": False}
    last = rows[-1]
    room = last.get("roomName") or last.get("room")
    floor = last.get("floorName") or last.get("floor")
    when = last.get("unixTs") or last.get("ts") or last.get("timestamp")
    return {
        "device_id": device_id.strip(),
        "found": True,
        "room": room,
        "floor": floor,
        "x": last.get("x"),
        "y": last.get("y"),
        "z": last.get("z"),
        "when": when,
    }
