"""High-level helpers for reading and editing the espresense config.yaml.

The companion REST API can READ the config (GET /api/state/config) but does
not expose a write endpoint. So mutations work by:

  1. Fetching the YAML from the running pod via kubectl exec + cat
  2. Loading it with ruamel.yaml so comments / order are preserved
  3. Mutating the in-memory structure
  4. Writing it back via kubectl exec + tee, leaving a timestamped .bak
  5. Optionally triggering a deployment restart

`fetch_yaml` and `push_yaml` are the two side-effecting primitives; the
domain modules (rooms.py, nodes.py) build on top of them with structured
edits.
"""

from __future__ import annotations

from typing import Any

from cli_anything.espresense.core import k8s_backend
from cli_anything.espresense.utils import yaml_io


def fetch_yaml(target: k8s_backend.K8sTarget) -> tuple[str, Any]:
    """Return (raw_text, parsed) for the live companion config.yaml."""
    raw = k8s_backend.read_config(target)
    parsed = yaml_io.load(raw)
    return raw, parsed


def push_yaml(target: k8s_backend.K8sTarget, parsed: Any, *,
              restart: bool = False, backup: bool = True) -> dict:
    """Serialize and push a modified config back to the pod.

    Returns a small summary dict (bytes written, restart status, etc).
    """
    text = yaml_io.dumps(parsed)
    k8s_backend.write_config(target, text, backup=backup)
    summary: dict = {
        "bytes_written": len(text.encode("utf-8")),
        "backed_up": bool(backup),
        "restarted": False,
    }
    if restart:
        k8s_backend.restart(target)
        summary["restarted"] = True
    return summary


def first_floor(parsed: Any) -> Any:
    """Pick the first floor; helpful for terse one-floor harnesses."""
    floors = parsed.get("floors") or []
    if not floors:
        raise KeyError("config has no `floors` block")
    return floors[0]


def find_floor(parsed: Any, floor_id: str) -> Any:
    for fl in parsed.get("floors") or []:
        if fl.get("id") == floor_id:
            return fl
    raise KeyError(f"no floor with id={floor_id!r}")
