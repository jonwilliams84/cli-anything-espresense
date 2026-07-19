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

# kubectl --timeout accepts a Go duration string: e.g. "120s", "5m", "1h",
# "1h30m", "500ms".  Only digits, dots, a leading minus, and the unit
# letters (n, s, u, m, h) are permitted — no shell metacharacters, spaces,
# semicolons, or additional kubectl flags.
_VALID_TIMEOUT_RE = re.compile(r"^-?[\d.]+[nsumh\u00b5]+([\d.]+[nsumh\u00b5]+)*\Z")

# Safe pattern for individual exec argv elements: allows spaces (since each
# element is a separate list argument, not shell-interpoled) but rejects shell
# metacharacters and control characters that could enable injection: ;, |, &,
# $, backticks, quotes, parentheses, <, >, \, !, null bytes, newlines, CR.
_VALID_EXEC_ARG_RE = re.compile(r"^[^;|&$`\"'()<>\\!\x00\n\r]*\Z")


def _check_path(label: str, value: str) -> str:
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


def _check_timeout(value: str) -> str:
    """Validate a kubectl --timeout value to prevent argument injection.

    kubectl accepts Go-style duration strings (e.g. ``120s``, ``5m``,
    ``1h30m``).  We reject anything containing shell metacharacters,
    whitespace, or embedded kubectl flags.
    """
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"timeout contains unsafe characters (got {value!r}). "
            "Only a Go duration string (digits and unit suffixes like "
            "s, m, h) is permitted."
        )
    return value


def _check_exec_arg(label: str, value: str) -> str:
    """Validate a single argv element for ``kubectl exec``.

    Each element becomes a separate argument to the command running inside
    the container.  We reject shell metacharacters, whitespace, null bytes,
    and anything that could break out of the argument list.
    """
    if not _VALID_EXEC_ARG_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "forward slashes, and the characters + = : , @ % are permitted."
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


def _sanitise_args(args: list[str]) -> list[str]:
    """Defence-in-depth: reject any argument containing null bytes or
    shell metacharacters before it reaches ``subprocess.run``.

    This is a backstop for the per-field validators above.  It catches
    values that were injected between validation and the subprocess call
    (e.g. via f-string interpolation) and ensures no unsanitised input
    ever reaches the kubectl invocation.
    """
    for i, arg in enumerate(args):
        if "\x00" in arg:
            raise ValueError(
                f"argument at position {i} contains a null byte "
                f"(got {arg!r})"
            )
    return args


def _run(
    args: list[str],
    *,
    stdin: Optional[bytes] = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Run a kubectl command. Raises if it fails (when check=True).

    All arguments are passed as a list to ``subprocess.run`` (never
    ``shell=True``) so there is no shell interpretation.  As a
    defence-in-depth measure, every argument is checked for null bytes
    before the call.
    """
    _sanitise_args(args)
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
    """Run a command inside the companion container.

    Every element of *argv* is validated with :func:`_check_exec_arg`
    before it is passed to kubectl so that shell metacharacters or
    embedded kubectl flags cannot be injected.
    """
    # Validate every user-supplied argv element before it reaches kubectl.
    safe_argv = [_check_exec_arg(f"exec argv[{i}]", v) for i, v in enumerate(argv)]
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
    # Validate the user-supplied timeout before it reaches kubectl so a
    # malicious value cannot inject extra flags or shell commands.
    _check_timeout(timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
