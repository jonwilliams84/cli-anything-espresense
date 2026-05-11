"""YAML round-trip helpers backed by ruamel.yaml.

We use ruamel so that loading and re-dumping the espresense config.yaml
preserves comments, key order, and quoting style as much as possible.
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def load(text: str) -> Any:
    return _yaml().load(text)


def load_path(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return _yaml().load(f)


def dumps(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def dump_path(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        _yaml().dump(data, f)
