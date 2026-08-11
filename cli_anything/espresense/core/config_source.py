"""Where a config.yaml is read from and written back to.

Until now every structured edit (rooms rename/rotate, nodes set-point, ...)
went straight through `k8s_backend`, so the whole editing surface required a
reachable Kubernetes cluster *and* a running companion pod. That made the
documented `config-fetch -> edit -> config-push` workflow impossible to
complete: you could get the YAML out and put it back, but nothing in between
could touch the local copy.

This module puts the "where does the YAML live" decision behind one small
interface with two implementations:

  K8sSource   — kubectl exec against the running companion pod (the default,
                behaviour-identical to what `config_yaml.fetch_yaml` /
                `push_yaml` did before).
  FileSource  — a local YAML file, for offline editing, review-before-apply
                planning, CI checks, and unit tests.

Both expose the same two primitives::

    raw, parsed = source.fetch()
    summary     = source.push(parsed, restart=..., backup=...)

so the CLI commands stay identical apart from picking a source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli_anything.espresense.core import k8s_backend
from cli_anything.espresense.utils import yaml_io


class ConfigSourceError(RuntimeError):
    """Raised when a config source cannot be read or written."""


@dataclass(frozen=True)
class K8sSource:
    """Read/write config.yaml inside the running companion pod via kubectl."""

    target: k8s_backend.K8sTarget

    kind = "k8s"

    def describe(self) -> str:
        return f"k8s://{self.target.namespace}/{self.target.deployment}{self.target.config_path}"

    def fetch(self) -> tuple[str, Any]:
        raw = k8s_backend.read_config(self.target)
        return raw, yaml_io.load(raw)

    def push(self, parsed: Any, *, restart: bool = False, backup: bool = True) -> dict:
        text = yaml_io.dumps(parsed)
        k8s_backend.write_config(self.target, text, backup=backup)
        summary: dict = {
            "source": self.kind,
            "bytes_written": len(text.encode("utf-8")),
            "backed_up": bool(backup),
            "restarted": False,
        }
        if restart:
            k8s_backend.restart(self.target)
            summary["restarted"] = True
        return summary


@dataclass(frozen=True)
class FileSource:
    """Read/write a config.yaml sitting on the local filesystem.

    `restart=True` is accepted but cannot mean anything here — there is no
    deployment to roll — so it is reported back as `restart_skipped` rather
    than silently ignored, otherwise a caller passing `--restart` would
    believe the companion had picked the change up.
    """

    path: Path

    kind = "file"

    def describe(self) -> str:
        return f"file://{self.path}"

    def fetch(self) -> tuple[str, Any]:
        p = Path(self.path)
        if not p.exists():
            raise ConfigSourceError(f"config file not found: {p}")
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigSourceError(f"cannot read {p}: {exc}") from exc
        return raw, yaml_io.load(raw)

    def push(self, parsed: Any, *, restart: bool = False, backup: bool = True) -> dict:
        p = Path(self.path)
        text = yaml_io.dumps(parsed)
        summary: dict = {
            "source": self.kind,
            "path": str(p),
            "bytes_written": len(text.encode("utf-8")),
            "backed_up": False,
            "restarted": False,
        }
        if backup and p.exists():
            bak = p.with_name(f"{p.name}.{int(time.time())}.bak")
            try:
                bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError as exc:
                raise ConfigSourceError(f"cannot write backup {bak}: {exc}") from exc
            summary["backed_up"] = True
            summary["backup_path"] = str(bak)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise ConfigSourceError(f"cannot write {p}: {exc}") from exc
        if restart:
            summary["restart_skipped"] = "local file source has no deployment to restart"
        return summary


def from_options(target: k8s_backend.K8sTarget, file: str | None = None):
    """Pick a source: an explicit local `file` wins, otherwise the pod."""
    if file:
        return FileSource(Path(file).expanduser())
    return K8sSource(target)
