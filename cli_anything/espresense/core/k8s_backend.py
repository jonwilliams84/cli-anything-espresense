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


# ---------------------------------------------------------------------------
# Validation helpers
#
# Every user-supplied value that flows into a kubectl argument is validated
# against a strict allow-list regex *before* it reaches subprocess.run.
# No shell strings are ever built; all kubectl invocations use argument lists.
# ---------------------------------------------------------------------------

# Kubernetes namespace names must be a DNS-1123 label: lowercase
# alphanumeric or hyphens, starting and ending with an alphanumeric
# character, at most 63 characters.  This is the strictest of the name
# patterns and is used for the ``-n`` flag in every kubectl call.
_VALID_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?\Z")
_MAX_NAME_LEN = 63

# Kubernetes resource names (deployments, pods, etc.) follow the DNS-1123
# subdomain rules: lowercase alphanumeric, hyphens, and dots, starting and
# ending with an alphanumeric character, at most 253 characters.  We use the
# stricter DNS-1123 label rules (no dots, max 63) for deployment and container
# names because the upstream chart never uses dots in those fields and the
# tighter bound closes more injection surface.
_VALID_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?\Z")

# Safe pattern for file paths inside the container: alphanumeric, slashes,
# dots, hyphens, underscores.  Rejects shell metacharacters, spaces, and
# null bytes that could be exploited in argument injection.
_VALID_PATH_RE = re.compile(r"^[\w./-]+\Z")

# Safe pattern for kubectl --timeout values: digits followed by an optional
# single time-unit suffix (s, m, h).  Rejects shell metacharacters, spaces,
# and anything that could be interpreted as an additional kubectl flag.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]?\Z")


def _check_namespace(value: str) -> str:
    """Validate a Kubernetes namespace name (DNS-1123 label)."""
    if not value or len(value) > _MAX_NAME_LEN or not _VALID_NAMESPACE_RE.match(value):
        raise ValueError(
            f"namespace contains unsafe characters (got {value!r}). "
            "Only lowercase alphanumeric characters and hyphens are permitted, "
            "starting and ending with an alphanumeric character (max 63 chars)."
        )
    return value


def _check_name(label: str, value: str) -> str:
    """Validate a Kubernetes resource name (DNS-1123 label)."""
    if not value or len(value) > _MAX_NAME_LEN or not _VALID_NAME_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only lowercase alphanumeric characters and hyphens are permitted, "
            "starting and ending with an alphanumeric character (max 63 chars)."
        )
    return value


def _check_path(label: str, value: str) -> str:
    """Validate a file path for use as a kubectl argument."""
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


def _check_timeout(value: str) -> str:
    """Validate a kubectl --timeout value."""
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"timeout contains unsafe characters (got {value!r}). "
            "Only digits with an optional 's', 'm', or 'h' suffix are permitted."
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
        # method is guaranteed to receive safe values.  Each field type has
        # a dedicated validator that enforces the strictest applicable
        # Kubernetes naming convention, preventing argument injection.
        object.__setattr__(
            self,
            "namespace",
            _check_namespace(self.namespace),
        )
        object.__setattr__(
            self,
            "deployment",
            _check_name("deployment", self.deployment),
        )
        object.__setattr__(
            self,
            "container",
            _check_name("container", self.container),
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
    proc = _run([
        "-n", target.namespace,
        "get", "pods",
        "-l", f"app={target.deployment}",
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
    args = [
        "-n", target.namespace,
        "exec",
        f"deploy/{target.deployment}",
        "-c", target.container,
    ]
    if stdin is not None:
        args.append("-i")
    args.append("--")
    args.extend(argv)
    payload = stdin.encode("utf-8") if stdin is not None else None
    return _run(args, stdin=payload, check=check, text=False)


def read_config(target: K8sTarget) -> str:
    """Read the companion's YAML config file out of the running pod."""
    proc = exec_(target, ["cat", target.config_path], check=True)
    return proc.stdout.decode("utf-8")


def write_config(target: K8sTarget, yaml_text: str, *, backup: bool = True) -> None:
    """Replace the companion's YAML config file inside the running pod.

    When backup=True, a timestamped copy of the existing file is left at
    <path>.<unix-ts>.bak before the overwrite, so the change is reversible.
    """
    # Generate the timestamp inside the pod so the backup file reflects the
    # pod's clock, not the host's.
    ts_proc = exec_(target, ["date", "+%s"], check=True)
    ts = int(ts_proc.stdout.decode("utf-8").strip())
    bak_path = f"{target.config_path}.{ts}.bak"
    if backup:
        exec_(target, [
            "cp", target.config_path, bak_path,
        ], check=False)
    # tee-by-stdin pattern: feed the file content as stdin, write with `dd`
    # so newlines and trailing whitespace are preserved verbatim.
    exec_(target, [
        "dd", f"of={target.config_path}",
    ], stdin=yaml_text, check=True)


def restart(target: K8sTarget) -> None:
    """Trigger a rolling restart of the companion deployment."""
    _run([
        "-n", target.namespace,
        "rollout", "restart",
        f"deployment/{target.deployment}",
    ], check=True)


def rollout_status(target: K8sTarget, timeout: str = "120s") -> str:
    timeout = _check_timeout(timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
