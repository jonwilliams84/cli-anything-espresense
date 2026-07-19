"""kubectl helpers for talking to an ESPresense-companion deployment.

Use this when the companion runs inside a Kubernetes cluster (which is the
typical deployment shape for ESPresense-companion). The functions here
shell out to `kubectl` — no in-process kube SDK dependency.

All operations target a deployment by name in a namespace and operate on a
specific container, with sane defaults for the upstream chart.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


# Safe pattern for Kubernetes resource names and file paths: alphanumeric,
# slashes, dots, hyphens, underscores.  Rejects shell metacharacters and
# null bytes that could be exploited in argument injection.
_VALID_PATH_RE = re.compile(r"^[\w./-]+\Z")


def _check_path(label: str, value: str) -> str:
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


# Safe pattern for a kubectl --timeout value: an optional integer count
# followed by a single time-unit suffix (s, m, h).  Rejects anything that
# could be interpreted as an additional kubectl flag or shell metacharacter.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]\Z")


def _check_timeout(value: str) -> str:
    """Validate a kubectl rollout --timeout value.

    kubectl accepts values like ``120s``, ``5m``, or ``1h``.  Anything else
    (including additional ``--flags``, spaces, or shell metacharacters) is
    rejected to prevent argument injection.
    """
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"timeout contains unsafe characters (got {value!r}). "
            "Only a positive integer followed by a single time-unit "
            "suffix (s, m, or h) is permitted, e.g. '120s' or '5m'."
        )
    return value


@dataclass(frozen=True)
class K8sTarget:
    namespace: str = "espresense"
    deployment: str = "espresense-companion"
    container: str = "espresense-companion"
    config_path: str = "/config/espresense/config.yaml"

    def __post_init__(self) -> None:
        # Validate all user-supplied fields at construction time so every
        # method is guaranteed to receive safe values.
        object.__setattr__(
            self,
            "namespace",
            _check_path("namespace", self.namespace),
        )
        object.__setattr__(
            self,
            "deployment",
            _check_path("deployment", self.deployment),
        )
        object.__setattr__(
            self,
            "container",
            _check_path("container", self.container),
        )
        object.__setattr__(
            self,
            "config_path",
            _check_path("config_path", self.config_path),
        )


def _kubectl() -> str:
    path = shutil.which("kubectl")
    if not path:
        raise RuntimeError(
            "kubectl not found on PATH. Install kubectl or set "
            "the CLI to talk to the companion's HTTP API directly "
            "(but config writes need filesystem access)."
        )
    return path


def _run(
    args: list[str],
    *,
    stdin: Optional[bytes] = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Run a kubectl command. Raises if it fails (when check=True)."""
    kc = _kubectl()
    proc = subprocess.run(
        [kc, *args],
        input=stdin,
        capture_output=True,
        text=text,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed (exit {proc.returncode}): {stderr}"
        )
    return proc


def pod_name(target: K8sTarget) -> str:
    """Resolve the running pod for the deployment."""
    # Defence-in-depth: re-validate user-supplied values at the point of
    # use so they can never reach _run unsanitised, even if a caller
    # bypassed K8sTarget.__post_init__.
    namespace = _check_path("namespace", target.namespace)
    deployment = _check_path("deployment", target.deployment)
    proc = _run([
        "-n", namespace,
        "get", "pods",
        "-l", f"app={deployment}",
        "-o", "jsonpath={.items[0].metadata.name}",
    ])
    name = (proc.stdout or "").strip()
    if not name:
        # fall back: deploy/<name> targeting
        return ""
    return name


def exec_(target: K8sTarget, argv: list[str], *, stdin: Optional[str] = None,
          check: bool = True) -> subprocess.CompletedProcess:
    """Run a command inside the companion container."""
    # Defence-in-depth: re-validate user-supplied values at the point of
    # use so they can never reach _run unsanitised, even if a caller
    # bypassed K8sTarget.__post_init__.
    namespace = _check_path("namespace", target.namespace)
    deployment = _check_path("deployment", target.deployment)
    container = _check_path("container", target.container)
    args = [
        "-n", namespace,
        "exec",
        f"deploy/{deployment}",
        "-c", container,
    ]
    if stdin is not None:
        args.append("-i")
    args.append("--")
    args.extend(argv)
    payload = stdin.encode("utf-8") if stdin is not None else None
    return _run(args, stdin=payload, check=check, text=False)


def read_config(target: K8sTarget) -> str:
    """Read the companion's YAML config file out of the running pod."""
    config_path = _check_path("config_path", target.config_path)
    proc = exec_(target, ["cat", config_path], check=True)
    return proc.stdout.decode("utf-8")


def write_config(target: K8sTarget, yaml_text: str, *, backup: bool = True) -> None:
    """Replace the companion's YAML config file inside the running pod.

    When backup=True, a timestamped copy of the existing file is left at
    <path>.<unix-ts>.bak before the overwrite, so the change is reversible.
    """
    # Defence-in-depth: re-validate config_path at the point of use.
    config_path = _check_path("config_path", target.config_path)
    # Generate the timestamp inside the pod so the backup file reflects the
    # pod's clock, not the host's.
    ts_proc = exec_(target, ["date", "+%s"], check=True)
    ts = int(ts_proc.stdout.decode("utf-8").strip())
    bak_path = f"{config_path}.{ts}.bak"
    if backup:
        exec_(target, [
            "cp", config_path, bak_path,
        ], check=False)
    # tee-by-stdin pattern: feed the file content as stdin, write with `dd`
    # so newlines and trailing whitespace are preserved verbatim.
    exec_(target, [
        "dd", f"of={config_path}",
    ], stdin=yaml_text, check=True)


def restart(target: K8sTarget) -> None:
    """Trigger a rolling restart of the companion deployment."""
    # Defence-in-depth: re-validate user-supplied values at the point of
    # use so they can never reach _run unsanitised, even if a caller
    # bypassed K8sTarget.__post_init__.
    namespace = _check_path("namespace", target.namespace)
    deployment = _check_path("deployment", target.deployment)
    _run([
        "-n", namespace,
        "rollout", "restart",
        f"deployment/{deployment}",
    ], check=True)


def rollout_status(target: K8sTarget, timeout: str = "120s") -> str:
    safe_timeout = _check_timeout(timeout)
    # Defence-in-depth: re-validate user-supplied values at the point of
    # use so they can never reach _run unsanitised, even if a caller
    # bypassed K8sTarget.__post_init__.
    namespace = _check_path("namespace", target.namespace)
    deployment = _check_path("deployment", target.deployment)
    proc = _run([
        "-n", namespace,
        "rollout", "status",
        f"deployment/{deployment}",
        f"--timeout={safe_timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
