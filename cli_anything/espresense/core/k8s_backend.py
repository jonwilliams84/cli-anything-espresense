"""kubectl helpers for talking to an ESPresense-companion deployment.

Use this when the companion runs inside a Kubernetes cluster (which is the
typical deployment shape for ESPresense-companion). The functions here
shell out to `kubectl` — no in-process kube SDK dependency.

All operations target a deployment by name in a namespace and operate on a
specific container, with sane defaults for the upstream chart.

Security: every user-supplied value is validated against a strict
allow-list before it can reach a ``kubectl`` invocation or a command run
inside the container.  No shell strings are ever constructed from
untrusted input — all commands are passed as argument lists to
``subprocess.run`` (never ``shell=True``).
"""

from __future__ import annotations

import re
import shlex
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


# Safe pattern for kubectl --timeout values: one or more digits followed by
# an optional single time-unit suffix (s, m, or h).  Rejects anything that
# could inject additional kubectl flags or arguments.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]?\Z")


def _check_timeout(label: str, value: str) -> str:
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only a non-negative integer with an optional time-unit suffix "
            "(s, m, or h) is permitted."
        )
    return value


# Safe pattern for individual command/argument tokens passed to
# ``kubectl exec ... -- <argv>``: alphanumeric plus a small set of
# punctuation that appears in legitimate container commands (slashes for
# paths, dots, hyphens, underscores, ``=`` for ``of=``/``--flag=value``
# style args, ``+`` and ``%`` for ``date +%s``, colons).  Rejects every
# shell metacharacter and null byte so a token can never be
# re-interpreted as more than one argument or as a command separator.
_VALID_ARGV_TOKEN_RE = re.compile(r"^[\w./+=:% -]+\Z")


def _check_argv_token(label: str, value: str) -> str:
    if not isinstance(value, str) or not _VALID_ARGV_TOKEN_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "forward slashes, colons, spaces, and the symbols = + % are "
            "permitted in command arguments."
        )
    return value


def _check_argv(label: str, argv: list[str]) -> list[str]:
    """Validate every element of an argv list before it reaches kubectl."""
    if not isinstance(argv, list) or not argv:
        raise ValueError(
            f"{label} must be a non-empty list of command arguments."
        )
    return [
        _check_argv_token(f"{label}[{i}]", tok) for i, tok in enumerate(argv)
    ]


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

    ``args`` is always a list passed directly to ``subprocess.run`` — it is
    never joined into a shell string for execution.  The only place the
    arguments are rendered as a string is the failure message, and there
    ``shlex.join`` is used so each token is safely quoted (preventing any
    ambiguous shell-string reconstruction of untrusted input).
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
            f"kubectl {shlex.join(args)} failed "
            f"(exit {proc.returncode}): {stderr}"
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
    """Run a command inside the companion container.

    Every element of ``argv`` is validated before it is placed on the
    kubectl argument list so that no user-supplied token can inject extra
    kubectl flags, command separators, or shell metacharacters.  The
    ``--`` separator precedes ``argv`` so kubectl never interprets its
    contents as options.
    """
    safe_argv = _check_argv("argv", argv)
    args = [
        "-n", target.namespace,
        "exec",
        f"deploy/{target.deployment}",
        "-c", target.container,
    ]
    if stdin is not None:
        args.append("-i")
    args.append("--")
    args.extend(safe_argv)
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
    # bak_path is derived from the already-validated config_path and an
    # integer timestamp, but validate it too for defense in depth so the
    # value is guaranteed safe before it reaches a container command.
    _check_path("bak_path", bak_path)
    if backup:
        exec_(target, [
            "cp", target.config_path, bak_path,
        ], check=False)
    # tee-by-stdin pattern: feed the file content as stdin, write with `dd`
    # so newlines and trailing whitespace are preserved verbatim.  The
    # destination path is a single validated argument, never interpolated
    # into a shell string.
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
    _check_timeout("timeout", timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
