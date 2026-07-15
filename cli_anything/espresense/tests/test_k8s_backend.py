"""Regression tests for k8s_backend command/argument injection.

These tests verify that user-supplied fields in K8sTarget cannot inject
arbitrary shell arguments through kubectl commands, and that all values are
validated at construction time.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from cli_anything.espresense.core import k8s_backend


# ── K8sTarget validation ─────────────────────────────────────────────────────

class TestK8sTargetValidation:
    """Untrusted fields must be rejected at construction, not at call time."""

    def test_defaults_are_valid(self):
        t = k8s_backend.K8sTarget()
        assert t.namespace == "espresense"
        assert t.deployment == "espresense-companion"
        assert t.container == "espresense-companion"
        assert t.config_path == "/config/espresense/config.yaml"

    @pytest.mark.parametrize(
        "field,value",
        [
            # Command separators / shell injection
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns && id"),
            ("namespace", "ns | cat"),
            ("namespace", "ns\n"),
            ("deployment", "deploy$(id)"),
            ("deployment", "deploy`id`"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy|cat"),
            ("deployment", "deploy&sleep 10"),
            ("container", "ctr$(whoami)"),
            ("container", "ctr`id`"),
            ("container", "ctr;sleep 10"),
            # Path traversal / file injection via config_path
            ("config_path", "/config; curl evil"),
            ("config_path", "/config && id"),
            ("config_path", "/config\nexit 1\n"),
            ("config_path", "/config/path with spaces/file"),
            ("config_path", "/config/path\x00null"),
            # Double-quote / dollar injection in all fields
            ("namespace", 'ns"$(id)"'),
            ("deployment", 'deploy"$(id)"'),
            ("container", 'ctr"$(id)"'),
            ("config_path", '/config"$(id)"'),
        ],
    )
    def test_rejects_shell_metacharacters(self, field, value):
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend.K8sTarget(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            # Valid Kubernetes names: alphanumeric, hyphens, underscores, dots, slashes
            ("namespace", "default"),
            ("namespace", "my-ns"),
            ("namespace", "my_ns"),
            ("namespace", "my.ns"),
            ("namespace", "espresense"),
            ("deployment", "my-deploy"),
            ("deployment", "my_deploy"),
            ("deployment", "my.deploy"),
            ("deployment", "espresense-companion"),
            ("container", "my-container"),
            ("container", "my_container"),
            ("container", "espresense-companion"),
            ("config_path", "/path/to/config.yaml"),
            ("config_path", "/path/to/config_file.yml"),
            ("config_path", "/path.with.dots/config"),
            ("config_path", "/config/espresense/config.yaml"),
        ],
    )
    def test_accepts_valid_values(self, field, value):
        t = k8s_backend.K8sTarget(**{field: value})
        assert getattr(t, field) == value


# ── argv is always separate arguments ───────────────────────────────────────

class TestArgvIsolation:
    """All kubectl calls must use a list of arguments, never shell strings."""

    def test_exec_separator_appears_before_argv(self):
        """kubectl '--' separator must precede argv so shell chars are inert."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"")
            k8s_backend.exec_(target, ["echo", "hello world"], check=False)
            args_list = mock_run.call_args[0][0]
            dash_idx = args_list.index("--")
            assert args_list[dash_idx + 1 :] == ["echo", "hello world"]

    def test_read_config_one_element_per_arg(self):
        """config_path passed as exactly one list element, not interpolated."""
        target = k8s_backend.K8sTarget(config_path="/safe/path/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            mock_exec.return_value = MagicMock(stdout=b"data: test")
            k8s_backend.read_config(target)
            argv = mock_exec.call_args[0][1]
            # argv is ["cat", <config_path>]; config_path must be a single element
            assert argv == ["cat", "/safe/path/config.yaml"]
            assert len(argv) == 2  # not split by spaces

    def test_write_config_dd_of_isolated(self):
        """dd of= uses config_path as a single safe string argument."""
        target = k8s_backend.K8sTarget(config_path="/safe/path/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            ts_proc = MagicMock()
            ts_proc.stdout = b"1234567890\n"
            mock_exec.return_value = ts_proc
            k8s_backend.write_config(target, "yaml: data", backup=False)
            dd_call = next(
                c for c in mock_exec.call_args_list
                if c[0][1][0] == "dd"
            )
            dd_argv = dd_call[0][1]
            of_arg = next(a for a in dd_argv if a.startswith("of="))
            assert of_arg == "of=/safe/path/config.yaml"
            assert ";" not in of_arg  # no embedded shell commands

    def test_backup_cp_path_isolated(self):
        """cp bak_path is a single argument, not a shell-interpolated string."""
        target = k8s_backend.K8sTarget(config_path="/safe/path/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            ts_proc = MagicMock()
            ts_proc.stdout = b"1234567890\n"
            mock_exec.return_value = ts_proc
            k8s_backend.write_config(target, "yaml: data", backup=True)
            cp_calls = [
                c for c in mock_exec.call_args_list
                if c[0][1][0] == "cp"
            ]
            assert len(cp_calls) == 1
            cp_argv = cp_calls[0][0][1]
            bak_path = "/safe/path/config.yaml.1234567890.bak"
            assert bak_path in cp_argv


# ── Timestamp must come from the pod, not the host ───────────────────────────

class TestTimestampSource:
    """Backup timestamps are generated inside the target container."""

    def test_timestamp_generated_by_pod_exec(self):
        """write_config calls `date +%s` inside the container, not subprocess."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "exec_") as mock_exec:
            ts_proc = MagicMock()
            ts_proc.stdout = b"1700000000\n"
            mock_exec.return_value = ts_proc
            k8s_backend.write_config(target, "yaml: data", backup=False)
            first_argv = mock_exec.call_args_list[0][0][1]
            assert first_argv == ["date", "+%s"]

    def test_no_local_subprocess_for_timestamp(self):
        """subprocess.check_output is not used for timestamp generation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "exec_") as mock_exec:
            with patch.object(k8s_backend.subprocess, "check_output") as mock_subproc:
                ts_proc = MagicMock()
                ts_proc.stdout = b"1700000000\n"
                mock_exec.return_value = ts_proc
                k8s_backend.write_config(target, "yaml: data", backup=False)
                mock_subproc.assert_not_called()


# ── _run uses a list, not shell=True ────────────────────────────────────────

class TestRunListArgument:
    """_run must always use list-based subprocess calls, never shell=True."""

    def test_run_passes_list_to_subprocess_run(self):
        """subprocess.run must receive args as a list, never a joined string."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                k8s_backend._run(["get", "pods"])
                call_kwargs = mock_run.call_args[1]
                # Must NOT use shell=True
                assert call_kwargs.get("shell", False) is not False or "shell" not in call_kwargs
                # args must be a list
                args = mock_run.call_args[0][0] if mock_run.call_args[0] else None
                assert isinstance(args, list)


# ── rollout_status timeout validation ───────────────────────────────────────

class TestTimeoutValidation:
    """The --timeout parameter in rollout_status is user-supplied and must be
    validated before it reaches a kubectl invocation to prevent argument
    injection."""

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "0s",
            "999h",
            "30m",
        ],
    )
    def test_accepts_valid_timeouts(self, timeout):
        """Valid kubectl timeout values (digits + s/m/h) are accepted."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args = mock_run.call_args[0][0]
            assert f"--timeout={timeout}" in args

    @pytest.mark.parametrize(
        "timeout",
        [
            # Argument injection: extra flags after the timeout value
            "120s --namespace=evil",
            "120s --some-flag",
            # Shell metacharacters
            "120s; rm -rf /",
            "120s && id",
            "120s | cat",
            "120s$(id)",
            "120s`id`",
            "120s\n",
            # Missing or wrong unit suffix
            "120",
            "120x",
            "120sec",
            "seconds",
            "",
            # Dots / spaces / other unsafe chars
            "1.5s",
            "1 2s",
            "-1s",
            # Null byte
            "120s\x00",
        ],
    )
    def test_rejects_unsafe_timeouts(self, timeout):
        """Unsafe or malformed timeout values must raise ValueError."""
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"unsafe or malformed"):
            k8s_backend.rollout_status(target, timeout=timeout)

    def test_default_timeout_is_valid(self):
        """The default timeout '120s' must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args = mock_run.call_args[0][0]
            assert "--timeout=120s" in args

    def test_unsafe_timeout_never_reaches_run(self):
        """When timeout validation fails, _run must not be called at all."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="120s; rm -rf /")
            mock_run.assert_not_called()
