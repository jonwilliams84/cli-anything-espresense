"""Calibration + autocalibration helpers."""

from __future__ import annotations

import math

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


def compute_rssi_at_1m(rssi: float, distance: float, absorption: float = 2.0) -> float:
    """Turn one measured reading into the rssi@1m value the config wants.

    ESPresense models received signal strength as
    ``rssi(d) = rssi@1m - 10 * n * log10(d)`` where ``n`` is the absorption
    exponent (the firmware's `absorption` setting, ~2.0 in free space). Given
    a reading ``rssi`` observed at a known distance ``d`` metres, that
    inverts to:

        rssi@1m = rssi + 10 * absorption * log10(distance)

    Pure maths — no I/O — so callers (and tests) can run it without any
    node, companion or broker. Raises ValueError for a non-positive distance
    or absorption; the caller decides how to report that.
    """
    rssi = float(rssi)
    distance = float(distance)
    absorption = float(absorption)
    if distance <= 0:
        raise ValueError("distance must be > 0")
    if absorption <= 0:
        raise ValueError("absorption must be > 0")
    return rssi + 10.0 * absorption * math.log10(distance)


def get(client: CompanionClient) -> dict:
    return companion_api.get_calibration(client)


def reset(client: CompanionClient) -> dict:
    return companion_api.reset_calibration(client)


def auto_optimize_get(client: CompanionClient) -> dict:
    return companion_api.get_auto_optimize(client)


def auto_optimize_set(client: CompanionClient, enabled: bool) -> dict:
    return companion_api.set_auto_optimize(client, enabled)


def summary(client: CompanionClient) -> dict:
    """A compact summary of calibration health for at-a-glance reporting."""
    cal = get(client)
    matrix = cal.get("matrix") if isinstance(cal, dict) else None
    if matrix is None:
        return {"r": cal.get("r"), "rmse": cal.get("rmse"), "pair_count": 0}
    pair_count = 0
    if isinstance(matrix, dict):
        for v in matrix.values():
            if isinstance(v, dict):
                pair_count += len(v)
    return {
        "r": cal.get("r"),
        "rmse": cal.get("rmse"),
        "pair_count": pair_count,
    }
