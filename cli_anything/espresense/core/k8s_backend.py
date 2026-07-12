"""kubectl helpers for talking to an ESPresense-companion deployment.

Use this when the companion runs inside a Kubernetes cluster (which is the
typical deployment shape for ESPresense-companion). The functions here
shell out to `kubectl` — no in-process kube SDK dependency.

All operations target a deployment by name in a namespace and operate on a
specific container, with sane defaults for the upstream chart.

Security note
-------------
Every user-supplied value (namespace, deployment, container, config_path,
timeout) is validated *before* it is incorporated into a kubectl argument
or a remote-shell command.  Values that contain shell metacharacters,
whitespace, or path-traversal sequences are rejected so that no
user-controlled string can break out of its intended argument position
and inject additional commands or flags.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


# ── input validation ────────────────────────────────────────────────────────
#
# Kubernetes resource names (namespace, deployment, container) must be DNS-1123
# labels/subdomains: lowercase alphanumerics, ``-`` and ``.`` only, starting and
# ending with an alphanumeric character.  We use a slightly relaxed pattern that
# still rejects every shell metacharacter, whitespace, path separators, and
# flag prefixes so that a validated value can never break out of its kubectl
# argument slot.
#
# ``config_path`` is a POSIX path inside the container, so it may contain ``/``
# but must not contain shell metacharacters, whitespace, command separators,
# or path-traversal (``..``) segments.
#
# ``timeout`` is a kubectl duration string (e.g. ``120s``, ``5m``); we allow
# alphanumerics only.

# Characters that are *never* allowed in any user-supplied k8s value because
# they are shell metacharacters or could break out of an argument.
_SHELL_METACHARS = set(";&|`$(){}[]<>\n\r\t\\\"' \0")

# Kubernetes resource-name pattern (DNS-1123 subdomain, relaxed to also allow
# the common ``-`` and ``.`` separators while rejecting everything dangerous).
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")

# POSIX path inside the container: alphanumerics, ``/``, ``-``, ``_``, ``.``,
# and ``~`` — but no shell metacharacters, no whitespace, no ``..`` traversal.
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._~/-]+$")

# kubectl duration: digits optionally followed by a single time unit.
_TIMEOUT_RE = re.compile(r"^[0-9]+[smh]?$")


class K8sValueError(ValueError):
    """Raised when a user-supplied k8s value fails validation."""


def _validate_name(value: str, field: str) -> str:
    """Validate a Kubernetes resource name (namespace, deployment, container).

    Rejects empty strings, values longer than 253 chars, and anything that
    is not a DNS-1123 subdomain.  This prevents shell/argument injection
    because the allowed character set excludes every shell metacharacter
    and whitespace.
    """
    if not value or not isinstance(value, str):
        raise K8sValueError(f"{field} must be a non-empty string")
    if len(value) > 253:
        raise K8sValueError(f"{field} must be at most 253 characters")
    if not _K8S_NAME_RE.match(value):
        raise K8sValueError(
            f"{field} contains invalid characters (must be a DNS-1123 subdomain: "
            f"lowercase alphanumerics, '-', '.')"
        )
    return value


def _validate_path(value: str, field: str = "config_path") -> str:
    """Validate a POSIX file path that will be used inside the container.

    Rejects empty strings, shell metacharacters, whitespace, and ``..``
    path-traversal segments so the path cannot break out of its argument
    position in a remote ``sh -c`` invocation or a kubectl argument.
    """
    if not value or not isinstance(value, str):
        raise K8sValueError(f"{field} must be a non-empty string")
    if any(ch in _SHELL_METACHARS for ch in value):
        raise K8sValueError(
            f"{field} contains shell metacharacters or whitespace"
        )
    if not _SAFE_PATH_RE.match(value):
        raise K8sValueError(
            f"{field} contains invalid characters"
        )
    # Reject path-traversal segments — ``..`` must never appear as a path
    # component, even if surrounded by ``/``.
    parts = value.split("/")
    if ".." in parts:
        raise K8sValueError(f"{field} must not contain '..' path-traversal segments")
    return value


def _validate_timeout(value: str) -> str:
    """Validate a kubectl rollout timeout string (e.g. ``120s``, ``5m``)."""
    if not value or not isinstance(value, str):
        raise K8sValueError("timeout must be a non-empty string")
    if not _TIMEOUT_RE.match(value):
        raise K8sValueError(
            "timeout must be a duration like '120s', '5m', or '1h' "
            "(digits optionally followed by s/m/h)"
        )
    return value


def _validate_target(target: "K8sTarget") -> "K8sTarget":
    """Validate every user-supplied field on a K8sTarget before use."""
    _validate_name(target.namespace, "namespace")
    _validate_name(target.deployment, "deployment")
    _validate_name(target.container, "container")
    _validate_path(target.config_path, "config_path")
    return target


@dataclass(frozen=True)
class K8sTarget:
    namespace: str = "espresense"
    deployment: str = "espresense-companion"
    container: str = "espresense-companion"
    config_path: str = "/config/espresense/config.yaml"


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
    """Run a command inside the companion container.

    ``argv`` is passed as a list of separate arguments to ``kubectl exec``
    so that no shell interpretation occurs on the caller side.  The
    ``target`` fields are validated before use.
    """
    _validate_target(target)
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

    The backup and write operations use ``cp``/``cat`` with the config path
    passed as a *separate argument* rather than interpolated into a
    ``sh -c`` string, so a malicious config_path cannot inject shell
    commands.
    """
    # Validate up front so we never pass an unsanitised value to kubectl.
    _validate_target(target)
    safe_path = target.config_path

    if backup:
        # Use a separate-argument ``sh -c`` where the only interpolated value
        # is the *validated* path, and the timestamp is generated by the shell
        # itself.  The path is passed as ``$0`` (a positional shell parameter)
        # so it is never re-interpreted by the shell even if it contained
        # metacharacters (defence in depth on top of validation).
        exec_(target, [
            "sh", "-c",
            'cp "$0" "$0".$(date +%s).bak',
            safe_path,
        ], check=False)
    # Write via stdin redirection: ``cat`` receives the path as ``$0`` so it
    # is never shell-interpolated.  The file content arrives on stdin.
    exec_(target, [
        "sh", "-c",
        'cat > "$0"',
        safe_path,
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
    _validate_target(target)
    _validate_timeout(timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
