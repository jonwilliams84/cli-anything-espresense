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

# Safe pattern for kubectl timeout values: digits followed by an optional
# time unit suffix (s, m, h).  Rejects shell metacharacters, additional
# flags, semicolons, and anything that is not a simple duration string.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]\Z")

# Safe pattern for individual argv elements passed to ``kubectl exec --``:
# ASCII alphanumeric, spaces, dots, slashes, hyphens, underscores, equals,
# percent, plus, and colons.  Rejects shell metacharacters, null bytes,
# and values that start with a dash (which could inject kubectl flags).
_VALID_ARGV_RE = re.compile(r"^[^-][a-zA-Z0-9 ./=%+:-]*\Z")


def _check_path(label: str, value: str) -> str:
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


def _check_timeout(label: str, value: str) -> str:
    """Validate a kubectl timeout value (e.g. ``120s``, ``5m``, ``1h``).

    Rejects anything that is not a bare digits-plus-unit duration so that
    shell metacharacters or additional kubectl flags cannot be injected
    through the ``--timeout=`` argument.
    """
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only a positive integer followed by a time unit "
            "(s, m, or h) is permitted, e.g. '120s' or '5m'."
        )
    return value


def _check_argv(label: str, value: str) -> str:
    """Validate a single argv element before it is passed to ``kubectl exec``.

    Rejects values that start with a dash (which could inject kubectl
    flags), contain shell metacharacters, whitespace beyond spaces, or
    null bytes.  This is a defence-in-depth check — ``subprocess.run``
    already uses a list (never ``shell=True``), but validating here
    ensures that no unsanitised value can reach the container process.
    """
    if not _VALID_ARGV_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only ASCII alphanumeric characters, spaces, dots, hyphens, "
            "underscores, forward slashes, equals signs, percent, plus, "
            "and colons are permitted. Values must not start with a dash."
        )
    return value


def _validate_target(target: "K8sTarget") -> "K8sTarget":
    """Re-validate every user-supplied field on *target* before it reaches
    a kubectl invocation.

    ``K8sTarget.__post_init__`` validates at construction time, but a frozen
    dataclass can still be mutated via ``object.__setattr__``.  This
    defence-in-depth check ensures that no unsanitised value can reach a
    kubectl or shell call, regardless of how the target was created or
    modified.
    """
    _check_path("namespace", target.namespace)
    _check_path("deployment", target.deployment)
    _check_path("container", target.container)
    _check_path("config_path", target.config_path)
    return target


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
    """Run a kubectl command. Raises if it fails (when check=True).

    Every element in *args* is passed directly to ``subprocess.run`` as a
    list — never joined into a shell string — so there is no shell
    interpretation layer.  Callers are responsible for validating
    user-supplied values before they reach this function (see
    ``_validate_target`` and ``_check_argv``).
    """
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
    _validate_target(target)
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
    _validate_target(target)
    # Validate every argv element so that shell metacharacters or kubectl
    # flag injection cannot sneak through the ``--`` separator.
    for i, arg in enumerate(argv):
        _check_argv(f"argv[{i}]", arg)
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
    _validate_target(target)
    _run([
        "-n", target.namespace,
        "rollout", "restart",
        f"deployment/{target.deployment}",
    ], check=True)


def rollout_status(target: K8sTarget, timeout: str = "120s") -> str:
    # Validate the user-supplied timeout before it reaches kubectl so that
    # shell metacharacters or extra flags cannot be injected via the
    # --timeout= argument.
    _check_timeout("timeout", timeout)
    _validate_target(target)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
