"""YAML round-trip helpers backed by ruamel.yaml.

We use ruamel so that loading and re-dumping the espresense config.yaml
preserves comments, key order, and quoting style as much as possible.
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq


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


def flow_seq(items: Any) -> CommentedSeq:
    """A sequence that dumps inline (`[1, 2, 3]`) rather than as a block.

    ESPresense configs are hand-authored with coordinates written compactly —
    `point: [1.0, 2.0, 2.5]`, `points: [[0, 0], [4, 0]]`. ruamel only preserves
    that style for nodes it *parsed*; anything the harness constructs from
    scratch defaults to block style, so an added room would render as a
    20-line ladder of `- - 5.0` next to its inline neighbours. Building new
    coordinate sequences through this helper keeps added entries visually
    consistent with the file they are being appended to.
    """
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq
