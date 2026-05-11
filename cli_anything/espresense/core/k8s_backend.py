"""kubectl helpers for talking to an ESPresense-companion deployment.

Use this when the companion runs inside a Kubernetes cluster (which is the
typical deployment shape for ESPresense-companion). The functions here
shell out to `kubectl` — no in-process kube SDK dependency.

All operations target a deployment by name in a namespace and operate on a
specific container, with sane defaults for the upstream chart.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


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
    if backup:
        exec_(target, [
            "sh", "-c",
            f"cp {target.config_path} {target.config_path}.$(date +%s).bak",
        ], check=False)
    # tee-by-stdin pattern: feed the file content as stdin, write with `dd`
    # so newlines and trailing whitespace are preserved verbatim.
    exec_(target, [
        "sh", "-c",
        f"cat > {target.config_path}",
    ], stdin=yaml_text, check=True)


def restart(target: K8sTarget) -> None:
    """Trigger a rolling restart of the companion deployment."""
    _run([
        "-n", target.namespace,
        "rollout", "restart",
        f"deployment/{target.deployment}",
    ], check=True)


def rollout_status(target: K8sTarget, timeout: str = "120s") -> str:
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
