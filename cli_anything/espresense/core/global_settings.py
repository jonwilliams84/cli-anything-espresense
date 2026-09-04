"""Companion global settings — the deployment-wide knobs *outside* config.yaml.

The companion keeps a set of global settings in its own state: telemetry
cadence, device expiration, availability timeout, the GPS origin, and the
include/exclude filters. They are not part of config.yaml, so neither
`settings` (which edits that file) nor any other existing command reaches
them. The companion exposes two transports for them:

  REST   GET  /api/settings        -> the full mapping
         POST /api/settings {k: v} -> apply one or more keys
  MQTT   espresense/settings/<key>/set   (retained; the companion applies the
         value on receipt and re-applies at startup)

This module models that surface for the harness: `fetch` reads with secret
redaction, `update` coerces a shell value the same way `settings set` does
and posts it, `describe` lists the keys we know about, and `mqtt.py` carries
the publish helper. Unknown keys are still sent through — the companion is
the schema — but `KNOWN_SETTINGS` documents the spellings that exist today.
"""

from __future__ import annotations

from typing import Any, Optional

from . import settings as settings_core
from .settings import SettingsError

__all__ = [
    "KNOWN_SETTINGS",
    "MQTT_TOPIC_FMT",
    "SETTINGS_API_PATH",
    "GlobalSettingsError",
    "describe",
    "fetch",
    "update",
]


class GlobalSettingsError(RuntimeError):
    """Raised for an unusable key, value, or a key the companion doesn't hold."""


# Where the companion serves its global settings over REST.
SETTINGS_API_PATH = "/api/settings"

# MQTT topic the companion subscribes to for one global setting.
MQTT_TOPIC_FMT = "{prefix}/settings/{key}/set"

# The keys the companion's GlobalSettings understands, and how a shell value
# for each is read. Kept declarative so `companion settings-keys` can show an
# agent what is available without round-tripping the companion first.
KNOWN_SETTINGS: dict[str, dict[str, str]] = {
    "telemetry": {
        "kind": "bool",
        "description": "whether nodes publish telemetry",
    },
    "count": {
        "kind": "bool",
        "description": "include a device count in device messages",
    },
    "expiration": {
        "kind": "int",
        "description": "seconds before a device's position is considered expired",
    },
    "availability_timeout": {
        "kind": "int",
        "description": "seconds before an offline node's devices are dropped",
    },
    "status_integrate": {
        "kind": "int",
        "description": "seconds of status messages to integrate before reporting",
    },
    "include": {
        "kind": "str",
        "description": "regex — only track device ids matching it",
    },
    "exclude": {
        "kind": "str",
        "description": "regex — never track device ids matching it",
    },
    "include_room": {
        "kind": "str",
        "description": "regex — only accept measurements from rooms matching it",
    },
    "exclude_room": {
        "kind": "str",
        "description": "regex — never accept measurements from rooms matching it",
    },
    "gps": {
        "kind": "json",
        "description": 'GPS origin object, e.g. {"lat":51.5,"lng":-0.1,"elev":30}',
    },
}


def describe() -> list[dict[str, str]]:
    """The known global setting keys, alphabetically, with kind + description."""
    return [
        {"key": key, "kind": meta["kind"], "description": meta["description"]}
        for key, meta in sorted(KNOWN_SETTINGS.items())
    ]


def _kind_for(key: str, kind: str) -> str:
    """The value kind to coerce with — the declared one unless overridden.

    Unknown keys fall through to `auto`, because the companion, not this
    module, owns the schema.
    """
    if kind != "auto":
        return kind
    return KNOWN_SETTINGS[key]["kind"] if key in KNOWN_SETTINGS else "auto"


def fetch(client: Any, key: Optional[str] = None, *, reveal: bool = False) -> dict:
    """GET /api/settings, redacted unless `reveal`.

    With `key`, returns just that setting: {key, found, value, secret}.
    Raises GlobalSettingsError for a key the companion does not hold.
    """
    raw = client.get(SETTINGS_API_PATH)
    if key is None:
        return {
            "source": SETTINGS_API_PATH,
            "settings": raw if reveal else settings_core.redact(raw),
        }
    if not isinstance(raw, dict) or key not in raw:
        raise GlobalSettingsError(
            f"the companion holds no global setting {key!r} — "
            "try `companion settings-keys` for the known spellings"
        )
    secret = settings_core.is_secret(key)
    value = raw[key]
    if secret and not reveal and value is not None:
        value = settings_core.REDACTED
    return {"key": key, "found": True, "value": value, "secret": secret}


def update(client: Any, key: str, value: Any, *, kind: str = "auto") -> dict:
    """Set one global setting: read the old value, POST {key: coerced}.

    The coercion reuses `settings.coerce` so `companion settings-set
    expiration 300` and `mqtt set-global` agree on what `true`, `300` and a
    JSON object mean. Secret-looking before/after values are masked in the
    returned summary — the output lands in transcripts.
    """
    if not str(key).strip() or "/" in str(key):
        raise GlobalSettingsError(f"setting key must be non-empty and contain no '/': {key!r}")
    try:
        coerced = settings_core.coerce(value, _kind_for(key, kind))
    except SettingsError as exc:
        raise GlobalSettingsError(str(exc)) from None

    raw = client.get(SETTINGS_API_PATH)
    before = raw.get(key) if isinstance(raw, dict) else None

    client.post(SETTINGS_API_PATH, json={key: coerced})

    secret = settings_core.is_secret(key)
    masked = secret and coerced is not None

    def _shown(v: Any) -> Any:
        if masked and v is not None:
            return settings_core.REDACTED
        return v

    return {
        "source": SETTINGS_API_PATH,
        "key": key,
        "before": _shown(settings_core.redact(before) if secret else before),
        "after": _shown(coerced),
        "secret": secret,
        "changed": before != coerced,
    }
