"""Calibration + autocalibration helpers."""

from __future__ import annotations

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


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
        return {"r": cal.get("r"), "rmse": cal.get("rmse"),
                 "pair_count": 0}
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
