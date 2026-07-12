"""Regression tests for command/argument-injection fixes in k8s_backend.

These tests verify that every user-supplied value that reaches a kubectl
argument or a remote ``sh -c`` invocation is validated *before* it is used,
so that no user-controlled string can break out of its intended argument
position and inject additional commands or flags.

The tests mock ``subprocess.run`` (via ``_run``) to capture the exact
argument list that would be passed to kubectl, and also test that
malicious values are rejected by the validation layer before any
subprocess call is made.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from cli_anything.espresense.core import k8s_backend
from cli_anything.espresense.core.k8s_backend import (
    K8sTarget,
    K8sValueError,
    _validate_name,
    _validate_path,
    _validate_timeout,
    _validate_target,
    pod_name,
    exec_,
    read_config,
    write_config,
    restart,
    rollout_status,
)


# ── helpers ──────────────────────────────────────────────────────────────────

VALID_TARGET = K8sTarget(
    namespace="espresense",
    deployment="espresense-companion",
    container="espresense-companion",
    config_path="/config/espresense/config.yaml",
)


def _fake_completed(returncode=0, stdout=b"", stderr=b""):
    """Create a fake CompletedProcess with bytes stdout/stderr."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _capture_run():
    """Patch k8s_backend._run and return a list that captures call args."""
    captured = []

    def fake_run(args, *, stdin=None, check=True, text=True):
        captured.append(list(args))
        # Respect the text parameter so stdout/stderr are the right type
        if text:
            return _fake_completed(stdout="ok\n", stderr="")
        return _fake_completed(stdout=b"ok\n", stderr=b"")

    return captured, fake_run

# ── _validate_name ───────────────────────────────────────────────────────────

class TestValidateName:
    def test_valid_names(self):
        for name in ["espresense", "espresense-companion", "my.app", "a", "abc123"]:
            assert _validate_name(name, "test") == name

    @pytest.mark.parametrize("bad", [
        "",
        None,
        "UPPERCASE",
        "has space",
        "has;semicolon",
        "has|pipe",
        "has`backtick",
        "has$dollar",
        "has(paren",
        "-leading-dash",
        ".leading-dot",
        "trailing-dash-",
        "trailing-dot.",
        "has/slash",
        "has\\backslash",
        "has\"quote",
        "has'quote",
        "has\nnewline",
        "has\ttab",
        "has\x00null",
    ])
    def test_rejects_invalid_names(self, bad):
        with pytest.raises(K8sValueError):
            _validate_name(bad, "test")

    def test_rejects_too_long(self):
        with pytest.raises(K8sValueError):
            _validate_name("a" * 254, "test")


# ── _validate_path ───────────────────────────────────────────────────────────

class TestValidatePath:
    def test_valid_paths(self):
        for path in [
            "/config/espresense/config.yaml",
            "/etc/config.yaml",
            "/tmp/test.bak",
            "/a/b/c/d/e.yaml",
            "/home/user/.config/app.conf",
        ]:
            assert _validate_path(path) == path

    @pytest.mark.parametrize("bad", [
        "",
        None,
        "/path with space",
        "/path;rm -rf /",
        "/path$(whoami)",
        "/path`whoami`",
        "/path|cat /etc/passwd",
        "/path\nnewline",
        "/path\x00null",
        "/path/../../../etc/passwd",
        "/path/..",
        "../etc/passwd",
        "/path/../../secret",
    ])
    def test_rejects_invalid_paths(self, bad):
        with pytest.raises(K8sValueError):
            _validate_path(bad)

    def test_rejects_path_traversal_in_middle(self):
        with pytest.raises(K8sValueError):
            _validate_path("/config/../../../etc/passwd")

    def test_rejects_double_dot_segment(self):
        with pytest.raises(K8sValueError):
            _validate_path("/config/../other.yaml")


# ── _validate_timeout ─────────────────────────────────────────────────────────

class TestValidateTimeout:
    @pytest.mark.parametrize("good", ["120s", "5m", "1h", "30", "0s", "999s"])
    def test_valid_timeouts(self, good):
        assert _validate_timeout(good) == good

    @pytest.mark.parametrize("bad", [
        "",
        None,
        "120s; rm -rf /",
        "120s && whoami",
        "120s | cat",
        "$(whoami)s",
        "120x",
        "abc",
        "12 0s",
        "120;s",
        "120\ns",
    ])
    def test_rejects_invalid_timeouts(self, bad):
        with pytest.raises(K8sValueError):
            _validate_timeout(bad)


# ── _validate_target ─────────────────────────────────────────────────────────

class TestValidateTarget:
    def test_valid_target_passes(self):
        result = _validate_target(VALID_TARGET)
        assert result is VALID_TARGET

    def test_rejects_bad_namespace(self):
        t = K8sTarget(namespace="bad ns", deployment="ok", container="ok")
        with pytest.raises(K8sValueError, match="namespace"):
            _validate_target(t)

    def test_rejects_bad_deployment(self):
        t = K8sTarget(namespace="ok", deployment="bad;rm", container="ok")
        with pytest.raises(K8sValueError, match="deployment"):
            _validate_target(t)

    def test_rejects_bad_container(self):
        t = K8sTarget(namespace="ok", deployment="ok", container="bad|cat")
        with pytest.raises(K8sValueError, match="container"):
            _validate_target(t)

    def test_rejects_bad_config_path(self):
        t = K8sTarget(config_path="/bad;rm -rf /")
        with pytest.raises(K8sValueError, match="config_path"):
            _validate_target(t)


# ── pod_name ──────────────────────────────────────────────────────────────────

class TestPodName:
    def test_valid_target_calls_kubectl_with_safe_args(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            pod_name(VALID_TARGET)
        # The deployment name should appear only as part of the label selector
        # and should not contain any injected characters.
        assert captured
        args = captured[0]
        assert "-n" in args
        assert "espresense" in args
        assert "app=espresense-companion" in args

    def test_rejects_malicious_deployment(self):
        target = K8sTarget(deployment="foo;rm -rf /")
        with pytest.raises(K8sValueError):
            pod_name(target)

    def test_rejects_malicious_namespace(self):
        target = K8sTarget(namespace="ns;whoami")
        with pytest.raises(K8sValueError):
            pod_name(target)


# ── exec_ ─────────────────────────────────────────────────────────────────────

class TestExec:
    def test_valid_target_calls_kubectl_safely(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            exec_(VALID_TARGET, ["cat", "/config/espresense/config.yaml"])
        assert captured
        args = captured[0]
        assert "deploy/espresense-companion" in args
        assert "-c" in args
        assert "espresense-companion" in args
        assert "--" in args
        assert "cat" in args
        assert "/config/espresense/config.yaml" in args

    def test_rejects_malicious_deployment(self):
        target = K8sTarget(deployment="foo;cat /etc/passwd")
        with pytest.raises(K8sValueError):
            exec_(target, ["cat", "/config/test.yaml"])

    def test_rejects_malicious_container(self):
        target = K8sTarget(container="foo;whoami")
        with pytest.raises(K8sValueError):
            exec_(target, ["cat", "/config/test.yaml"])

    def test_rejects_malicious_namespace(self):
        target = K8sTarget(namespace="ns;whoami")
        with pytest.raises(K8sValueError):
            exec_(target, ["cat", "/config/test.yaml"])


# ── read_config ───────────────────────────────────────────────────────────────

class TestReadConfig:
    def test_valid_target(self):
        fake_cp = _fake_completed(stdout=b"yaml: content\n")
        with patch.object(k8s_backend, "_run", return_value=fake_cp):
            result = read_config(VALID_TARGET)
        assert result == "yaml: content\n"

    def test_rejects_malicious_config_path(self):
        target = K8sTarget(config_path="/bad;rm -rf /")
        with pytest.raises(K8sValueError):
            read_config(target)


# ── write_config ──────────────────────────────────────────────────────────────

class TestWriteConfig:
    def test_valid_target_backup_and_write(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            write_config(VALID_TARGET, "yaml: content\n", backup=True)
        # Two calls: backup + write
        assert len(captured) == 2

        def _extract_script(args):
            """Find the sh -c script string in a kubectl exec arg list.

            The args look like: [-n, ns, exec, deploy/..., -c, container, --, sh, -c, <script>, <path>]
            We need the script that follows the *second* '-c' (the one after '--' and 'sh').
            """
            dashdash = args.index("--")
            after = args[dashdash + 1:]
            assert after[0] == "sh"
            assert after[1] == "-c"
            return after[2]

        # Backup call: sh -c 'cp "$0" "$0".$(date +%s).bak' <safe_path>
        backup_args = captured[0]
        backup_script = _extract_script(backup_args)
        assert "$0" in backup_script
        assert "cp" in backup_script
        # The safe path should be passed as a separate argument after the script
        assert VALID_TARGET.config_path in backup_args
        # The script must NOT contain the raw path interpolated
        assert VALID_TARGET.config_path not in backup_script

        # Write call: sh -c 'cat > "$0"' <safe_path>
        write_args = captured[1]
        write_script = _extract_script(write_args)
        assert "$0" in write_script
        assert "cat" in write_script
        assert VALID_TARGET.config_path in write_args
        assert VALID_TARGET.config_path not in write_script

    def test_valid_target_no_backup(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            write_config(VALID_TARGET, "yaml: content\n", backup=False)
        # Only one call: write
        assert len(captured) == 1
        write_args = captured[0]

        def _extract_script(args):
            dashdash = args.index("--")
            after = args[dashdash + 1:]
            assert after[0] == "sh"
            assert after[1] == "-c"
            return after[2]

        script = _extract_script(write_args)
        assert "$0" in script
        assert "cat" in script
        assert VALID_TARGET.config_path in write_args
        assert VALID_TARGET.config_path not in script

    def test_rejects_malicious_config_path(self):
        target = K8sTarget(config_path="/safe; rm -rf /")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_config_path_with_command_substitution(self):
        target = K8sTarget(config_path="/config/$(whoami).yaml")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_config_path_with_backticks(self):
        target = K8sTarget(config_path="/config/`whoami`.yaml")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_config_path_with_pipe(self):
        target = K8sTarget(config_path="/config/x|cat /etc/passwd")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_config_path_with_newline(self):
        target = K8sTarget(config_path="/config/x\nrm -rf /")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_config_path_with_semicolon(self):
        target = K8sTarget(config_path="/config/x;rm -rf /")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_malicious_deployment(self):
        target = K8sTarget(deployment="foo;rm -rf /")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")

    def test_rejects_path_traversal(self):
        target = K8sTarget(config_path="/config/../../../etc/passwd")
        with pytest.raises(K8sValueError):
            write_config(target, "yaml: content\n")


# ── restart ───────────────────────────────────────────────────────────────────

class TestRestart:
    def test_valid_target(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            restart(VALID_TARGET)
        assert captured
        args = captured[0]
        assert "deployment/espresense-companion" in args
        assert "rollout" in args
        assert "restart" in args

    def test_rejects_malicious_deployment(self):
        target = K8sTarget(deployment="foo;rm -rf /")
        with pytest.raises(K8sValueError):
            restart(target)

    def test_rejects_malicious_namespace(self):
        target = K8sTarget(namespace="ns;whoami")
        with pytest.raises(K8sValueError):
            restart(target)


# ── rollout_status ────────────────────────────────────────────────────────────

class TestRolloutStatus:
    def test_valid_target_and_timeout(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            rollout_status(VALID_TARGET, timeout="60s")
        assert captured
        args = captured[0]
        assert "deployment/espresense-companion" in args
        assert "--timeout=60s" in args

    def test_default_timeout(self):
        captured, fake_run = _capture_run()
        with patch.object(k8s_backend, "_run", side_effect=fake_run):
            rollout_status(VALID_TARGET)
        assert captured
        args = captured[0]
        assert "--timeout=120s" in args

    def test_rejects_malicious_deployment(self):
        target = K8sTarget(deployment="foo;rm -rf /")
        with pytest.raises(K8sValueError):
            rollout_status(target)

    def test_rejects_malicious_timeout(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="60s;rm -rf /")

    def test_rejects_malicious_timeout_with_pipe(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="60s|cat /etc/passwd")

    def test_rejects_malicious_timeout_with_command_substitution(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="$(whoami)s")

    def test_rejects_malicious_timeout_with_backticks(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="`whoami`")

    def test_rejects_malicious_timeout_with_newline(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="60s\nrm -rf /")

    def test_rejects_malicious_timeout_with_ampersand(self):
        with pytest.raises(K8sValueError):
            rollout_status(VALID_TARGET, timeout="60s&&whoami")


# ── integration: no subprocess called with malicious input ────────────────────

class TestNoSubprocessWithMaliciousInput:
    """Ensure that validation happens *before* any subprocess.run call."""

    def test_pod_name_no_subprocess_on_bad_input(self):
        target = K8sTarget(deployment="bad;rm -rf /")
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                pod_name(target)
            mock_run.assert_not_called()

    def test_exec_no_subprocess_on_bad_input(self):
        target = K8sTarget(container="bad;rm -rf /")
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                exec_(target, ["cat", "/test"])
            mock_run.assert_not_called()

    def test_write_config_no_subprocess_on_bad_input(self):
        target = K8sTarget(config_path="/bad;rm -rf /")
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                write_config(target, "yaml: content\n")
            mock_run.assert_not_called()

    def test_restart_no_subprocess_on_bad_input(self):
        target = K8sTarget(deployment="bad;rm -rf /")
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                restart(target)
            mock_run.assert_not_called()

    def test_rollout_status_no_subprocess_on_bad_input(self):
        target = K8sTarget(deployment="bad;rm -rf /")
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                rollout_status(target, timeout="60s")
            mock_run.assert_not_called()

    def test_rollout_status_no_subprocess_on_bad_timeout(self):
        with patch.object(k8s_backend.subprocess, "run") as mock_run:
            with pytest.raises(K8sValueError):
                rollout_status(VALID_TARGET, timeout="60s;rm -rf /")
            mock_run.assert_not_called()
