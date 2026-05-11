"""Connection-profile management for cli-anything-espresense.

Stores companion URL / kubectl target / MQTT broker / per-node defaults in
~/.config/cli-anything-espresense.json so users don't have to pass --base-url
on every command.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "cli-anything-espresense.json"

DEFAULTS: dict[str, Any] = {
    # Companion HTTP API
    "base_url": "http://localhost:8267",
    "verify_ssl": True,
    "timeout": 30,
    # Kubernetes (used by `config push` and `companion restart`)
    "k8s_namespace": "espresense",
    "k8s_deployment": "espresense-companion",
    "k8s_container": "espresense-companion",
    "k8s_config_path": "/config/espresense/config.yaml",
    # MQTT broker (for direct setting publishes to nodes)
    "mqtt_host": None,
    "mqtt_port": 1883,
    "mqtt_username": None,
    "mqtt_password": None,
    "mqtt_topic_prefix": "espresense",
    # Per-node HTTP defaults (each node has its own IP)
    "node_http_port": 80,
    "node_http_timeout": 10,
}


def load_config(path: Optional[Path] = None) -> dict:
    p = path or DEFAULT_CONFIG_PATH
    out = dict(DEFAULTS)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                out.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    # env overrides (handy for one-off shell sessions)
    for k in list(out.keys()):
        env = "CLI_ESPRESENSE_" + k.upper()
        if env in os.environ:
            v = os.environ[env]
            # coerce ints/bools roughly
            if isinstance(DEFAULTS.get(k), bool):
                out[k] = v.lower() in ("1", "true", "yes", "on")
            elif isinstance(DEFAULTS.get(k), int):
                try:
                    out[k] = int(v)
                except ValueError:
                    out[k] = v
            else:
                out[k] = v
    return out


def save_config(cfg: dict, path: Optional[Path] = None) -> Path:
    p = path or DEFAULT_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    return p


def merge_cli_overrides(cfg: dict, **kwargs) -> dict:
    """Merge CLI flag values (None means unset, so leave defaults alone)."""
    out = dict(cfg)
    for k, v in kwargs.items():
        if v is not None:
            out[k] = v
    return out
