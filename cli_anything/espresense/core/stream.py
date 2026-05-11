"""WebSocket live event stream for the companion's /ws endpoint."""

from __future__ import annotations

import json
import time
from typing import Callable, Optional
from urllib.parse import urlparse

try:
    import websocket  # type: ignore
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore


def _ws_url(base_url: str, show_all: bool = False) -> str:
    p = urlparse(base_url)
    scheme = "wss" if p.scheme == "https" else "ws"
    netloc = p.netloc or p.path
    query = "?showAll=true" if show_all else ""
    return f"{scheme}://{netloc}/ws{query}"


def stream(base_url: str, *, show_all: bool = False,
           duration: Optional[float] = None,
           types: Optional[set[str]] = None,
           callback: Optional[Callable[[dict], None]] = None) -> list[dict]:
    """Connect to /ws and collect events.

    types: set of event `type` values to keep ({"deviceChanged", "nodeStateChanged"}).
            None means keep all.
    duration: seconds to listen; None = until KeyboardInterrupt.
    """
    if websocket is None:
        raise RuntimeError(
            "websocket-client is not installed — pip install websocket-client "
            "or reinstall the harness."
        )

    url = _ws_url(base_url, show_all=show_all)
    collected: list[dict] = []
    end = (time.time() + duration) if duration else None
    ws = websocket.create_connection(url, timeout=5)
    try:
        ws.settimeout(0.5)
        while True:
            if end and time.time() >= end:
                break
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                event = {"raw": raw}
            if types and event.get("type") not in types:
                continue
            collected.append(event)
            if callback:
                try:
                    callback(event)
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return collected
