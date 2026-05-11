"""Device-history wrappers."""

from __future__ import annotations

from typing import Optional

from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient


def get_history(client: CompanionClient, device_id: str,
                start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    resp = companion_api.get_device_history(client, device_id, start=start, end=end)
    if isinstance(resp, dict):
        return resp.get("history") or []
    return resp if isinstance(resp, list) else []
