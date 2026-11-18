"""Device tracking helpers — wrappers over the companion's device API.

v0.16.0 closes the runtime half of the per-device filter story: v0.15.0 gave
the durable config.yaml side (`config_devices.py`, `devices ...-config`) the
six location filters, but the *runtime* copy the companion applies live
(`PUT /api/device/<id>`) could only be edited in its identity fields. The
companion accepts the same filter fields there, so `update_device` now
carries them too — no config push and no restart needed to retune a noisy
beacon.

Key spellings for the runtime PUT mirror what this module already sends:
`name`, `RefRssi`, `anchored`, and the camelCase filter keys the companion's
device object exposes (`idPrefixFilter`, ...). One deliberate asymmetry with
the config side: there are no `--clear-*` twins here, because the runtime
store cannot express "absent key" — a cleared filter is a *durable* change
(and the only surface that can express it is config.yaml), so it lives on
`devices update-in-config --clear-*` + restart. Setting a filter to an empty
string would merely mean "forgot the value", so empty strings are refused.
"""

from __future__ import annotations

from typing import Any

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


class DeviceSettingsError(RuntimeError):
    """Raised for a runtime device edit the companion would reject."""


# Runtime PUT key for each filter field, mirroring the camelCase keys the
# companion's device object already exposes elsewhere (configuredRefRssi...).
RUNTIME_FILTER_KEYS: dict[str, str] = {
    "include": "include",
    "exclude": "exclude",
    "id_prefix_filter": "idPrefixFilter",
    "accuracy": "accuracy",
    "distance": "distance",
}


def list_devices(client: CompanionClient, show_all: bool = False) -> list[dict]:
    raw = companion_api.list_devices(client, show_all=show_all)
    out: list[dict] = []
    for d in raw:
        out.append(
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "room": (d.get("room") or {}).get("name")
                if isinstance(d.get("room"), dict)
                else d.get("room"),
                "floor": (d.get("floor") or {}).get("name")
                if isinstance(d.get("floor"), dict)
                else d.get("floor"),
                "last_seen": d.get("lastHit") or d.get("lastSeen"),
                "x": d.get("x"),
                "y": d.get("y"),
                "z": d.get("z"),
                "confidence": d.get("confidence"),
                "ref_rssi": d.get("configuredRefRssi") or d.get("refRssi"),
                "raw": d,
            }
        )
    return out


def get_device(client: CompanionClient, device_id: str) -> dict:
    return companion_api.get_device(client, device_id)


def _coerce_filter(field: str, value: Any) -> Any:
    """Coerce one runtime filter value to the shape the companion accepts.

    Same guard rails the durable config side applies (`config_devices.
    _coerce_filter`): strings must be non-empty, the accuracy/distance clamps
    must be numbers > 0 (a clamp of 0 would disable the device), and integral
    clamps stay integral rather than shipping `7.0` in JSON.
    """
    if field in ("accuracy", "distance"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DeviceSettingsError(f"{field} must be a number, got {value!r}")
        number = float(value)
        if not number > 0:
            raise DeviceSettingsError(
                f"{field} must be > 0 (a clamp of {value} disables the device)"
            )
        return int(number) if number == int(number) else number
    text = str(value).strip()
    if not text:
        raise DeviceSettingsError(f"{field} must be a non-empty filter string")
    return text


def update_device(
    client: CompanionClient,
    device_id: str,
    *,
    name: str | None = None,
    ref_rssi: int | None = None,
    anchored_x: float | None = None,
    anchored_y: float | None = None,
    anchored_z: float | None = None,
    discovery: bool | None = None,
    include: str | None = None,
    exclude: str | None = None,
    id_prefix_filter: str | None = None,
    accuracy: float | None = None,
    distance: float | None = None,
) -> dict | None:
    """PUT a partial settings update for one tracked device.

    Identity (name/ref_rssi/anchors), the discovery toggle and the six
    location filters are all accepted; only the fields actually given are
    sent, so the runtime copy keeps everything else.
    """
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
    if discovery is not None:
        settings["discovery"] = bool(discovery)
    values = {
        "include": include,
        "exclude": exclude,
        "id_prefix_filter": id_prefix_filter,
        "accuracy": accuracy,
        "distance": distance,
    }
    for field, value in values.items():
        if value is not None:
            settings[RUNTIME_FILTER_KEYS[field]] = _coerce_filter(field, value)
    if not settings:
        return None
    return companion_api.put_device(client, device_id, settings)


def delete_device(client: CompanionClient, device_id: str) -> None:
    companion_api.delete_device(client, device_id)
