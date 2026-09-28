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


def _join(path: str, key: str) -> str:
    """Dotted-path join: `mqtt` + `password` -> `mqtt.password`."""
    return f"{path}.{key}" if path else str(key)


def _leaf_key(path: str) -> str:
    """The key a path ends in, with any list index stripped (`a.b[0]` -> `b`)."""
    seg = path.rsplit(".", 1)[-1]
    while seg.endswith("]") and "[" in seg:
        seg = seg[: seg.rindex("[")]
    return seg


def _diff_walk(a: Any, b: Any, path: str, out: list[dict]) -> None:
    """Collect differences between two parsed config docs into `out`.

    Dicts recurse by key; lists recurse positionally (`[i]` paths) — so a
    reordered list is reported as per-element changes, which is honest but
    noisy, and is documented as such. Anything else is compared with `==`.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            child = _join(path, str(key))
            if key in b:
                _diff_walk(a[key], b[key], child, out)
            else:
                out.append({"path": child, "kind": "removed", "old": a[key], "new": None})
        for key in b:
            if key not in a:
                out.append(
                    {"path": _join(path, str(key)), "kind": "added", "old": None, "new": b[key]}
                )
    elif isinstance(a, list) and isinstance(b, list):
        common = min(len(a), len(b))
        for i in range(common):
            _diff_walk(a[i], b[i], f"{path}[{i}]", out)
        for i in range(common, len(a)):
            out.append({"path": f"{path}[{i}]", "kind": "removed", "old": a[i], "new": None})
        for i in range(common, len(b)):
            out.append({"path": f"{path}[{i}]", "kind": "added", "old": None, "new": b[i]})
    elif a != b:
        out.append({"path": path or "$", "kind": "changed", "old": a, "new": b})


def diff_configs(a: Any, b: Any, *, redact_secrets: bool = True) -> dict:
    """Semantic diff between two parsed config.yaml documents.

    Built for drift detection: the companion only reloads config.yaml on
    start, so its *running* view (GET /api/state/config) can silently lag the
    deployed file. `a` is treated as the older/live-er side and `b` as the
    on-disk side: a key present only in `b` is `added` (pushed but not picked
    up), only in `a` is `removed`, and present in both with different values
    is `changed`.

    Paths are dotted with `[i]` for list indexes (`rooms[0].points[1]`).
    Secret leaves (`settings.SECRET_HINTS`, e.g. `mqtt.password`) are masked
    on both sides by default — this output routinely lands in transcripts —
    pass `redact_secrets=False` to see the raw values.

    Pure: no I/O, no transport. The CLI command owns fetching both sides.
    """
    from cli_anything.espresense.core import settings as settings_core

    diffs: list[dict] = []
    _diff_walk(a, b, "", diffs)
    if redact_secrets:
        for d in diffs:
            if settings_core.is_secret(_leaf_key(d["path"])):
                d["old"] = d["new"] = settings_core.REDACTED
    return {"identical": not diffs, "differences": diffs}


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
