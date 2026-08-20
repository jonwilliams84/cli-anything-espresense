"""The non-structural half of config.yaml: tuning scalars and toggle sections.

`floors:` / `rooms:` / `nodes:` / `devices:` describe *what exists*, and every
one of them now has dedicated commands. Everything else in an ESPresense
companion config describes *how it behaves* — `timeout`, `away_timeout`,
`gps:`, `mqtt:`, `locators:` (nadaraya_watson, nelder_mead, nearest_node,
gauss_newton...), `optimization:`, `history:`, `weighting:`, `filtering:` —
and none of it was reachable. Switching localisation algorithm or widening a
timeout meant hand-editing YAML, i.e. the exact step this harness removes.

Design decisions worth keeping:

  * **Schema-free.** Companion releases add and rename tuning keys often. A
    hardcoded option per key would rot; a dotted path (`locators.nelder_mead.
    enabled`) works against whatever the running version's config contains,
    and `settings show` reports what is actually there instead of what this
    module was written against.
  * **Structural blocks are refused, not silently allowed.** `settings set
    nodes.0.room X` would bypass every consistency guarantee `rooms.py` and
    `validate.py` provide (and index-addressing YAML lists is a footgun), so
    those paths raise and name the command to use instead.
  * **Secrets are redacted by default.** `mqtt.password` lives in this half of
    the file. `settings show` is the kind of output that ends up pasted into
    an issue or handed to an agent's transcript, so it redacts unless
    `--reveal` is passed explicitly.

Pure: parsed-config in, structured result out. No I/O, no YAML text.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Blocks with dedicated command groups. Editing them through a generic path
# setter would skip the cross-reference repair those commands do.
STRUCTURAL: dict[str, str] = {
    "floors": "floors",
    "rooms": "rooms",
    "nodes": "nodes",
    "devices": "devices ...-config",
}

# Substrings that make a key's value a secret. Matched case-insensitively on
# the leaf key name only, so `mqtt.password` redacts but a room named
# "Key Room" does not.
SECRET_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key")

REDACTED = "***"

# Sections summarised by count rather than dumped in full by `show`.
_LIST_SECTIONS = ("floors", "nodes", "devices", "device_trackers")


class SettingsError(ValueError):
    """Raised for an unusable settings path or value."""


def is_secret(key: Any) -> bool:
    name = str(key).lower()
    return any(hint in name for hint in SECRET_HINTS)


def redact(value: Any) -> Any:
    """Deep-copy `value` into plain Python with secret leaves masked."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if is_secret(k) and v is not None else redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def split_path(path: str) -> list[str]:
    """`a.b.c` -> ['a','b','c'], rejecting empty segments early."""
    if path is None or not str(path).strip():
        raise SettingsError("path must be non-empty, e.g. 'locators.nelder_mead.enabled'")
    parts = [p.strip() for p in str(path).split(".")]
    if any(not p for p in parts):
        raise SettingsError(f"path {path!r} has an empty segment")
    return parts


def _guard(parts: list[str]) -> None:
    root = parts[0]
    if root in STRUCTURAL and len(parts) > 1:
        raise SettingsError(
            f"{root!r} is edited with the `{STRUCTURAL[root]}` commands, not `settings set` — "
            "they keep cross-references consistent"
        )


def coerce(value: Any, kind: str = "auto") -> Any:
    """Turn a shell string into the YAML scalar it obviously means.

    `auto` reads bools, null, ints, floats and JSON literals; the explicit
    kinds exist for the cases where the obvious reading is wrong (a device
    name of "true", a string port number).
    """
    text = value if isinstance(value, str) else str(value)
    if kind == "str":
        return text
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            raise SettingsError(f"{text!r} is not an integer") from None
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            raise SettingsError(f"{text!r} is not a number") from None
    if kind == "bool":
        lowered = text.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise SettingsError(f"{text!r} is not a boolean")
    if kind == "json":
        try:
            return json.loads(text)
        except ValueError as exc:
            raise SettingsError(f"invalid JSON: {exc}") from None
    if kind != "auto":
        raise SettingsError(f"unknown value type {kind!r}")

    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~"):
        return None
    if stripped[:1] in ("[", "{"):
        try:
            return json.loads(stripped)
        except ValueError:
            return text
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return text


def get_path(parsed: Any, path: str, *, reveal: bool = False) -> dict:
    """Read one dotted path. Returns {path, found, value, secret}."""
    parts = split_path(path)
    node: Any = parsed
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return {"path": path, "found": False, "value": None, "secret": False}
        node = node[part]
    secret = is_secret(parts[-1]) and node is not None
    value = node if reveal else redact({parts[-1]: node})[parts[-1]]
    return {"path": path, "found": True, "value": value, "secret": secret}


def set_path(parsed: Any, path: str, value: Any, *, kind: str = "auto") -> dict:
    """Set one dotted path, creating intermediate mappings as needed.

    Returns {path, before, after, created, secret}. `created` lists the
    parent mappings this call had to invent, so `settings set` can say it
    added a whole `locators.nelder_mead:` block rather than one key.
    """
    parts = split_path(path)
    _guard(parts)
    if not isinstance(parsed, dict):
        raise SettingsError("config root is not a YAML mapping")
    coerced = coerce(value, kind)
    node: Any = parsed
    created: list[str] = []
    for i, part in enumerate(parts[:-1]):
        existing = node.get(part)
        if existing is None:
            node[part] = {}
            created.append(".".join(parts[: i + 1]))
        elif not isinstance(existing, dict):
            raise SettingsError(
                f"{'.'.join(parts[: i + 1])!r} holds a {type(existing).__name__}, "
                "not a mapping — cannot descend into it"
            )
        node = node[part]
    leaf = parts[-1]
    before = node.get(leaf)
    node[leaf] = coerced
    secret = is_secret(leaf)
    return {
        "path": path,
        "before": REDACTED if secret and before is not None else before,
        "after": REDACTED if secret and coerced is not None else coerced,
        "created": created,
        "secret": secret,
    }


def unset_path(parsed: Any, path: str) -> dict:
    """Delete one dotted path. Returns {path, removed, before}."""
    parts = split_path(path)
    _guard(parts)
    if parts[0] in STRUCTURAL and len(parts) == 1:
        raise SettingsError(
            f"refusing to delete the whole {parts[0]!r} block — "
            f"use the `{STRUCTURAL[parts[0]]}` commands"
        )
    node: Any = parsed
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return {"path": path, "removed": False, "before": None}
        node = node[part]
    leaf = parts[-1]
    if not isinstance(node, dict) or leaf not in node:
        return {"path": path, "removed": False, "before": None}
    before = node[leaf]
    del node[leaf]
    return {
        "path": path,
        "removed": True,
        "before": REDACTED if is_secret(leaf) and before is not None else redact(before),
    }


def summary(parsed: Any, *, section: Optional[str] = None, reveal: bool = False) -> dict:
    """What the behaviour half of the config currently says.

    Structural blocks collapse to counts (`floors: 2 floor(s)`) so the output
    stays readable next to the tuning keys, which are shown in full (redacted).
    """
    if not isinstance(parsed, dict):
        raise SettingsError("config root is not a YAML mapping")
    if section is not None:
        if section not in parsed:
            raise KeyError(f"config has no {section!r} section")
        value = parsed.get(section)
        return {section: value if reveal else redact(value)}
    out: dict = {}
    for key, value in parsed.items():
        if key in _LIST_SECTIONS:
            count = len(value) if isinstance(value, list) else 0
            out[key] = f"<{count} entr{'y' if count == 1 else 'ies'}>"
            continue
        out[key] = value if reveal else redact(value)
    return out


def resolve_section(parsed: Any, *candidates: str) -> Optional[str]:
    """First candidate key present in the config (companion renamed some)."""
    if not isinstance(parsed, dict):
        return None
    for name in candidates:
        if isinstance(parsed.get(name), dict):
            return name
    return None


def list_toggles(parsed: Any, section: str) -> list[dict]:
    """Rows for a `{name: {enabled: bool, ...}}` section (locators, optimizers).

    `enabled` defaults to True when the key is absent, matching the
    companion: a locator block that exists but says nothing is on.
    """
    block = parsed.get(section) if isinstance(parsed, dict) else None
    if block is None:
        return []
    if not isinstance(block, dict):
        raise SettingsError(f"{section!r} is not a mapping")
    rows: list[dict] = []
    for name, cfg in block.items():
        if isinstance(cfg, dict):
            enabled = cfg.get("enabled", True)
            params = {k: v for k, v in cfg.items() if k != "enabled"}
        else:
            # scalar form, e.g. `optimization: true`
            enabled = bool(cfg)
            params = {}
        rows.append(
            {
                "section": section,
                "name": name,
                "enabled": bool(enabled),
                "params": redact(params) or None,
            }
        )
    return rows


def set_toggle(parsed: Any, section: str, name: str, enabled: bool) -> dict:
    """Flip `<section>.<name>.enabled`. Returns {section, name, before, after}."""
    block = parsed.get(section) if isinstance(parsed, dict) else None
    if not isinstance(block, dict) or name not in block:
        raise KeyError(f"config has no {section}.{name}")
    cfg = block[name]
    if not isinstance(cfg, dict):
        before = bool(cfg)
        block[name] = {"enabled": bool(enabled)}
        return {"section": section, "name": name, "before": before, "after": bool(enabled)}
    before = bool(cfg.get("enabled", True))
    cfg["enabled"] = bool(enabled)
    return {"section": section, "name": name, "before": before, "after": bool(enabled)}
