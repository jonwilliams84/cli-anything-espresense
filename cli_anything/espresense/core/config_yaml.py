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


def fetch_yaml(target: k8s_backend.K8sTarget) -> tuple[str, Any]:
    """Return (raw_text, parsed) for the live companion config.yaml.

    Kept as the stable pod-only entry point. The CLI now routes through
    `config_source`, which supports a local file as well; this delegates to
    the same implementation so the two cannot drift apart.
    """
    from cli_anything.espresense.core.config_source import K8sSource

    return K8sSource(target).fetch()


def push_yaml(
    target: k8s_backend.K8sTarget, parsed: Any, *, restart: bool = False, backup: bool = True
) -> dict:
    """Serialize and push a modified config back to the pod.

    Returns a small summary dict (bytes written, restart status, etc).
    Delegates to `config_source.K8sSource` — see `fetch_yaml`.
    """
    from cli_anything.espresense.core.config_source import K8sSource

    return K8sSource(target).push(parsed, restart=restart, backup=backup)


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


def list_floors(parsed: Any) -> list[dict]:
    """Summarise every floor: id, name, bounds, room count, node count."""
    out: list[dict] = []
    for fl in parsed.get("floors") or []:
        fid = fl.get("id")
        room_names = [r.get("name") for r in (fl.get("rooms") or [])]
        node_count = 0
        for node in parsed.get("nodes") or []:
            node_floors = node.get("floors") or []
            if fid in node_floors:
                node_count += 1
            elif not node_floors and (node.get("room") or "").strip() in room_names:
                node_count += 1
        out.append(
            {
                "id": fid,
                "name": fl.get("name"),
                "bounds": fl.get("bounds"),
                "room_count": len(room_names),
                "room_names": room_names,
                "node_count": node_count,
            }
        )
    return out
