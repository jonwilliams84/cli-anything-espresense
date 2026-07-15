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

# kubectl --timeout accepts a duration like "30s", "5m", "1h", or "0".
# Only digits and a single time-unit suffix are permitted, rejecting any
# characters that could be used for argument injection.
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]$|^\d+$")

# Shell metacharacters and control characters that must never appear in a
# kubectl argument token.  Even though ``subprocess.run`` is called with a
# list (no ``shell=True``), rejecting these characters is defence-in-depth
# against any future regression and makes injection attempts fail loudly.
# Spaces are intentionally allowed because each list element is a separate
# argument when ``shell`` is not used; newlines, tabs, and other control
# characters are still rejected.
_SHELL_META_RE = re.compile(r"[`$|;&<>\n\r\t\\\"\x00]")

# Exact kubectl flags that this module hardcodes.  Tokens matching one of
# these are internally-constructed and therefore safe.
_SAFE_EXACT_FLAGS = frozenset({"-n", "-c", "-i", "-l", "-o", "--"})

# Internally-constructed kubectl flag=value prefixes.  Tokens starting with
# one of these prefixes are produced by this module from already-validated
# values (K8sTarget fields, timeout, etc.) and are therefore safe.  Any
# other token that starts with ``-`` is rejected to prevent argument
# injection (e.g. a user-supplied ``--as=system:admin``).
_SAFE_FLAG_VALUE_PREFIXES = (
    "app=",
    "jsonpath=",
    "of=",
    "--timeout=",
    "deploy/",
    "deployment/",
)


def _check_path(label: str, value: str) -> str:
    if not _VALID_PATH_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only alphanumeric characters, dots, hyphens, underscores, "
            "and forward slashes are permitted."
        )
    return value


def _check_timeout(label: str, value: str) -> str:
    """Validate a kubectl --timeout duration string.

    Accepts values like ``30s``, ``5m``, ``1h``, or a bare integer (seconds).
    Rejects anything containing shell metacharacters, spaces, or extra flags
    that could be used for argument injection.
    """
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"{label} is not a valid timeout duration (got {value!r}). "
            "Expected a number of seconds optionally followed by 's', "
            "'m', or 'h' (e.g. '120s', '5m', '1h')."
        )
    return value


def _is_safe_flag(value: str) -> bool:
    """Return True if *value* is an internally-constructed kubectl flag."""
    if value in _SAFE_EXACT_FLAGS:
        return True
    return value.startswith(_SAFE_FLAG_VALUE_PREFIXES)


def _check_arg(label: str, value: str) -> str:
    """Validate a single kubectl argument token for injection safety.

    Rejects shell metacharacters (``; & | $ ` < > \\ "``), control
    characters (newlines, tabs, carriage returns), and null bytes that
    could be used for command or argument injection.  Spaces are allowed
    because each list element is a separate argument when ``shell`` is
    not used.  This is the defence-in-depth
    gatekeeper applied at the ``_run`` choke-point so that no
    unsanitised user-supplied value can ever reach ``subprocess.run``.

    Values starting with ``-`` are rejected unless they are one of the
    internally-constructed kubectl flags (see ``_SAFE_EXACT_FLAGS`` and
    ``_SAFE_FLAG_VALUE_PREFIXES``); a user-supplied value that looks like
    a flag (e.g. ``--as=system:admin``) would constitute argument
    injection.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be a string (got {type(value).__name__})."
        )
    if "\x00" in value:
        raise ValueError(
            f"{label} contains a null byte (got {value!r})."
        )
    if _SHELL_META_RE.search(value):
        raise ValueError(
            f"{label} contains unsafe shell metacharacters (got {value!r}). "
            "Shell operators, quotes, backticks, dollar signs, and "
            "whitespace are not permitted in kubectl arguments."
        )
    if value.startswith("-") and not _is_safe_flag(value):
        raise ValueError(
            f"{label} looks like an injected flag (got {value!r}). "
            "User-supplied values must not start with '-'."
        )
    return value


def _check_argv(label: str, argv: list[str]) -> list[str]:
    """Validate every token in an argv list before it reaches kubectl.

    Each element is checked with :func:`_check_arg` so that no
    shell metacharacter or injected flag can slip through the
    ``--`` separator into the container command.
    """
    if not isinstance(argv, list):
        raise ValueError(
            f"{label} must be a list of strings (got {type(argv).__name__})."
        )
    return [_check_arg(f"{label}[{i}]", v) for i, v in enumerate(argv)]


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

    Every argument token is validated by :func:`_check_arg` as a
    defence-in-depth measure so that no unsanitised user-supplied value
    can ever reach ``subprocess.run``.  This is the final gatekeeper:
    even if a caller bypasses a higher-level validator, the injection
    attempt is caught here before any subprocess is spawned.
    """
    safe_args = [_check_arg(f"args[{i}]", arg) for i, arg in enumerate(args)]
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

    ``argv`` is the list of command tokens to execute inside the container
    (after kubectl's ``--`` separator).  Every token is validated by
    :func:`_check_argv` to prevent command or argument injection.
    """
    argv = _check_argv("argv", argv)
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
    # Defence-in-depth: validate the derived backup path even though it is
    # built from already-validated components (config_path + integer ts).
    _check_path("bak_path", bak_path)
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
    timeout = _check_timeout("timeout", timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
