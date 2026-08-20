"""The `devices:` registry inside config.yaml — the tracked-beacon allow-list.

There are two completely different "devices" in an ESPresense deployment and
the harness only covered one of them:

  * runtime devices — whatever the companion currently sees over MQTT, served
    by /api/state/devices and edited by PUT /api/device/{id}. That is
    `core/devices.py` and the `devices list|show|set|delete` commands. Those
    edits live in the companion's own store and say nothing about config.yaml.
  * configured devices — the `devices:` block of config.yaml, which is what
    makes a beacon *tracked by name* across restarts and pins its reference
    RSSI. Nothing in the harness could read or write it, so the one durable
    half of device management still meant hand-editing YAML.

This module is the config half, and it is pure: parsed-config in, structured
result out, no I/O. It mirrors `nodes.py` deliberately — same `add` /
`remove` shape, same duplicate refusal, same `{found, before, after}` result
dicts — because `devices add-to-config` and `nodes add` are the same gesture
against different blocks.

The reference-RSSI key is `rssi@1m` on disk (that is the companion's YAML
alias). `@` is awkward in shell and JSON-path contexts, so the CLI spells it
`--rssi-at-1m` and the result dicts report `rssi_at_1m`; `RSSI_KEY` is the
single place the on-disk spelling is written. Configs in the wild also carry
the underscored spelling, so reads accept both and writes normalise to the
alias the companion actually parses.
"""

from __future__ import annotations

from typing import Any, Optional

from cli_anything.espresense.utils import yaml_io

RSSI_KEY = "rssi@1m"
_RSSI_ALIASES = (RSSI_KEY, "rssi_at_1m")

# Keys the summary rows promote to columns; anything else is preserved on
# disk but reported under `extra` so an unknown future field is never lost.
_KNOWN = {"id", "name", *_RSSI_ALIASES}


class DeviceConfigError(ValueError):
    """Raised for a device edit that would corrupt the `devices:` block."""


def _devices(parsed: Any) -> list:
    return parsed.get("devices") or []


def _read_rssi(entry: Any) -> Optional[float]:
    for key in _RSSI_ALIASES:
        if key in entry:
            value = entry.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _row(entry: Any, index: int) -> dict:
    extra = {k: v for k, v in entry.items() if k not in _KNOWN}
    return {
        "index": index,
        "id": entry.get("id"),
        "name": entry.get("name"),
        "rssi_at_1m": _read_rssi(entry),
        "extra": extra or None,
    }


def list_devices(parsed: Any) -> list[dict]:
    """Every entry of `devices:` as a flat row (id, name, rssi_at_1m, extra)."""
    return [_row(entry, i) for i, entry in enumerate(_devices(parsed)) if isinstance(entry, dict)]


def find(parsed: Any, device_id: str) -> Optional[dict]:
    """The raw YAML mapping for `device_id`, or None. Mutating it edits the config."""
    for entry in _devices(parsed):
        if isinstance(entry, dict) and entry.get("id") == device_id:
            return entry
    return None


def get(parsed: Any, device_id: str) -> dict:
    """One device as a summary row. Raises KeyError when absent."""
    for i, entry in enumerate(_devices(parsed)):
        if isinstance(entry, dict) and entry.get("id") == device_id:
            return _row(entry, i)
    raise KeyError(f"no device with id={device_id!r} in config.yaml")


def add(
    parsed: Any,
    device_id: str,
    *,
    name: Optional[str] = None,
    rssi_at_1m: Optional[float] = None,
) -> dict:
    """Append a tracked device. Refuses duplicate ids.

    The companion keys `devices:` by `id`, so a second entry with the same id
    shadows the first exactly like a duplicate node name does — better to
    refuse than to write a config whose behaviour depends on list order.
    """
    if device_id is None or not str(device_id).strip():
        raise DeviceConfigError("device id must be non-empty")
    device_id = str(device_id).strip()
    if find(parsed, device_id) is not None:
        raise DeviceConfigError(f"device {device_id!r} already exists in config.yaml")
    if parsed.get("devices") is None:
        parsed["devices"] = []
    entry: dict = {"id": _id_scalar(device_id)}
    if name is not None:
        entry["name"] = str(name)
    if rssi_at_1m is not None:
        entry[yaml_io.dq(RSSI_KEY)] = _coerce_rssi(rssi_at_1m)
    parsed["devices"].append(entry)
    return {
        "added": True,
        "id": device_id,
        "name": entry.get("name"),
        "rssi_at_1m": _read_rssi(entry),
    }


def _id_scalar(device_id: str) -> Any:
    """Quote ids that carry a colon (`irk:abc`) so they read like the rest of
    the file; leave plain ones plain."""
    return yaml_io.dq(device_id) if any(c in device_id for c in ":#@ ") else device_id


def _coerce_rssi(value: Any) -> Any:
    """Keep integral reference RSSIs integral — -65 reads better than -65.0."""
    number = float(value)
    return int(number) if number == int(number) else number


# Distinguishes "caller did not mention this field" from "caller passed None
# to clear it"; `update(name=None)` must not silently wipe a device's name.
_UNSET = object()


def update(
    parsed: Any,
    device_id: str,
    *,
    name: Any = _UNSET,
    rssi_at_1m: Any = _UNSET,
    new_id: Optional[str] = None,
) -> dict:
    """Edit one device in place. Returns {found, changed, before, after}.

    Passing an explicit None for `name`/`rssi_at_1m` removes that key (so a
    mis-set reference RSSI can be dropped back to the global default rather
    than only ever overwritten). Renaming the id refuses a collision, for the
    same reason `add` does.
    """
    entry = find(parsed, device_id)
    if entry is None:
        return {"found": False, "changed": [], "before": None, "after": None}
    before = _row(entry, -1)
    changed: list[str] = []

    if new_id is not None and str(new_id).strip() != device_id:
        candidate = str(new_id).strip()
        if not candidate:
            raise DeviceConfigError("new device id must be non-empty")
        if find(parsed, candidate) is not None:
            raise DeviceConfigError(f"device {candidate!r} already exists in config.yaml")
        entry["id"] = _id_scalar(candidate)
        changed.append("id")

    if name is not _UNSET:
        if name is None:
            if entry.pop("name", None) is not None:
                changed.append("name")
        elif entry.get("name") != str(name):
            entry["name"] = str(name)
            changed.append("name")

    if rssi_at_1m is not _UNSET:
        if rssi_at_1m is None:
            removed = False
            for key in _RSSI_ALIASES:
                if key in entry:
                    del entry[key]
                    removed = True
            if removed:
                changed.append("rssi_at_1m")
        else:
            value = _coerce_rssi(rssi_at_1m)
            # normalise onto the alias the companion parses, dropping variants
            for key in _RSSI_ALIASES:
                if key != RSSI_KEY and key in entry:
                    del entry[key]
            if entry.get(RSSI_KEY) != value:
                # a key added now is quoted like the hand-authored ones; a key
                # already in the file keeps whatever style it was written in
                key = RSSI_KEY if RSSI_KEY in entry else yaml_io.dq(RSSI_KEY)
                entry[key] = value
                changed.append("rssi_at_1m")

    after = _row(entry, -1)
    before.pop("index", None)
    after.pop("index", None)
    return {"found": True, "changed": changed, "before": before, "after": after}


def remove(parsed: Any, device_id: str) -> dict:
    """Drop a device from `devices:`. Returns {removed, id, entry}."""
    devices = _devices(parsed)
    for i, entry in enumerate(devices):
        if isinstance(entry, dict) and entry.get("id") == device_id:
            row = _row(entry, i)
            del devices[i]
            return {"removed": True, "id": device_id, "entry": row}
    return {"removed": False, "id": device_id, "entry": None}
