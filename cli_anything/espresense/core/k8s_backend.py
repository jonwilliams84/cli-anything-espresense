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

# Safe pattern for kubectl --timeout values: digits followed by an optional
# single-letter time unit (s, m, h).  Rejects shell metacharacters, spaces,
# semicolons, and anything that could break out of the --timeout= argument.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]?\Z")

# Characters that must never appear in a single kubectl argument.  These
# enable command injection (if the call ever regresses to shell=True) or
# argument injection (null bytes, newlines that can split or truncate
# arguments at the C level).  Curly braces, square brackets, equals, plus,
# and percent are intentionally NOT included because they appear in valid
# kubectl expressions such as jsonpath={...} and label selectors.
_UNSAFE_ARG_RE = re.compile(r"[\x00-\x1f;|`$()<>!\\\"'&]")


def _check_path(label: str, value: str) -> str:
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


def _check_timeout(label: str, value: str) -> str:
    """Validate a kubectl --timeout value before it reaches a kubectl call.

    Accepts strings like ``120s``, ``5m``, or ``1h``.  Rejects anything
    containing shell metacharacters, whitespace, or null bytes that could
    be used for argument injection.
    """
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only digits followed by an optional time unit "
            "(s, m, or h) are permitted."
        )
    return value


def _check_arg(label: str, value: str) -> str:
    """Validate a single kubectl argument before it reaches subprocess.run.

    This is the defence-in-depth gate inside ``_run``: every argument that
    flows into a ``kubectl`` invocation is checked here so that shell
    metacharacters, control characters, and null bytes are rejected
    regardless of whether the caller validated the value upstream in
    ``K8sTarget.__post_init__``.

    Kubectl flag tokens (e.g. ``-n``, ``--timeout=``) are safe because they
    are constructed by this module from validated components, but the
    *value* portion of each argument is still scanned so that a value like
    ``deploy/$(id)`` can never slip through.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{label} must be a str, got {type(value).__name__}"
        )
    if _UNSAFE_ARG_RE.search(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Shell metacharacters, control characters, and null bytes "
            "are not permitted in kubectl arguments."
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
    """Run a kubectl command. Raises if it fails (when check=True).

    Every argument is validated by ``_check_arg`` before it reaches
    ``subprocess.run`` so that no unsanitised user-supplied value can
    inject shell commands or extra kubectl flags.
    """
    safe_args = [_check_arg(f"args[{i}]", a) for i, a in enumerate(args)]
    kc = _kubectl()
    proc = subprocess.run(
        [kc, *safe_args],
        input=stdin,
        capture_output=True,
        text=text,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(
            f"kubectl {' '.join(safe_args)} failed (exit {proc.returncode}): {stderr}"
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
    # Validate the user-supplied timeout before it reaches kubectl so
    # shell metacharacters cannot be injected via the --timeout argument.
    _check_timeout("timeout", timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
