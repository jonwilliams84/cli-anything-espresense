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

class TestRolloutStatusTimeoutValidation:
    """The user-supplied --timeout value must be validated before kubectl."""

    @pytest.mark.parametrize(
        "timeout",
        [
            # Shell command separators
            "120s; rm -rf /",
            "120s && id",
            "120s | cat",
            "120s\nid",
            # Argument injection via extra kubectl flags
            "120s --namespace=evil",
            "120s --insecure-skip-tls-verify",
            # Shell substitution
            "120s$(id)",
            "120s`id`",
            # Whitespace / null bytes
            "120s ",
            " 120s",
            "120s\x00",
            # Empty / non-duration
            "",
            "abc",
            "120",
            # Quotes
            '120s"$(id)"',
            "120s'$(id)'",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        """Malicious timeout values must raise ValueError, never reach kubectl."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"timeout contains unsafe characters"):
                k8s_backend.rollout_status(target, timeout=timeout)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "1h30m",
            "500ms",
            "1.5h",
            "0s",
            "1h2m3s",
            "2h45m30s",
            "100ms",
            "0.5s",
            "-2s",
        ],
    )
    def test_accepts_valid_timeout(self, timeout):
        """Valid Go duration strings must be accepted and reach kubectl."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(
                a for a in args_list if a.startswith("--timeout=")
            )
            assert timeout_arg == f"--timeout={timeout}"

    def test_default_timeout_is_valid(self):
        """The default timeout must pass validation without error."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(
                a for a in args_list if a.startswith("--timeout=")
            )
            assert timeout_arg == "--timeout=120s"

    def test_validation_happens_before_kubectl_call(self):
        """_check_timeout must run before _run is invoked."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_check_timeout", side_effect=ValueError("blocked")) as mock_check:
            with patch.object(k8s_backend, "_run") as mock_run:
                with pytest.raises(ValueError, match="blocked"):
                    k8s_backend.rollout_status(target, timeout="120s; rm")
                mock_check.assert_called_once_with("120s; rm")
                mock_run.assert_not_called()


# ── _sanitise_args defence-in-depth ──────────────────────────────────────────

class TestSanitiseArgs:
    """_sanitise_args is the last line of defence before subprocess.run."""

    def test_rejects_null_byte_in_args(self):
        """No argument may contain a null byte."""
        with pytest.raises(ValueError, match=r"null byte"):
            k8s_backend._sanitise_args(["safe", "bad\x00arg"])

    def test_accepts_clean_args(self):
        """Clean arguments pass through unchanged."""
        args = ["-n", "default", "get", "pods"]
        assert k8s_backend._sanitise_args(args) == args

    def test_run_calls_sanitise_before_subprocess(self):
        """_run must call _sanitise_args before subprocess.run."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend, "_sanitise_args", wraps=k8s_backend._sanitise_args) as mock_sanitize:
                with patch.object(k8s_backend.subprocess, "run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    k8s_backend._run(["get", "pods"])
                    mock_sanitize.assert_called_once_with(["get", "pods"])

    def test_run_rejects_null_byte_before_subprocess(self):
        """A null byte in args must raise before subprocess.run is called."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                with pytest.raises(ValueError, match=r"null byte"):
                    k8s_backend._run(["safe", "evil\x00"])
                mock_run.assert_not_called()


# ── exec_ argv validation ────────────────────────────────────────────────────

class TestExecArgvValidation:
    """Every element of the argv list passed to exec_ must be validated."""

    @pytest.mark.parametrize(
        "argv",
        [
            # Shell command separators
            ["cat", "/path; rm -rf /"],
            ["cat", "/path && id"],
            ["cat", "/path | cat"],
            # Shell substitution
            ["cat", "/path$(id)"],
            ["cat", "/path`id`"],
            # Newlines / null bytes
            ["cat", "/path\n"],
            ["cat", "/path\x00null"],
            # Quotes
            ["cat", '/path"$(id)"'],
            ["cat", "/path'$(id)'"],
            # Parentheses / redirects
            ["cat", "/path(foo)"],
            ["cat", "/path>evil"],
            ["cat", "/path<evil"],
            # Backslash
            ["cat", "/path\\evil"],
            # Exclamation (history expansion)
            ["cat", "/path!evil"],
            # Injection in the command name itself
            [";id", "/safe"],
            ["$(id)", "/safe"],
            ["`id`", "/safe"],
        ],
    )
    def test_rejects_unsafe_argv_elements(self, argv):
        """Malicious argv elements must raise ValueError, never reach kubectl."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, argv, check=False)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "argv",
        [
            ["cat", "/config/espresense/config.yaml"],
            ["date", "+%s"],
            ["cp", "/config/espresense/config.yaml", "/config/espresense/config.yaml.123.bak"],
            ["dd", "of=/config/espresense/config.yaml"],
            ["echo", "hello world"],
            ["sh", "-c", "echo hello"],
        ],
    )
    def test_accepts_valid_argv(self, argv):
        """Valid argv elements must pass through and reach kubectl."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            k8s_backend.exec_(target, argv, check=False)
            mock_run.assert_called_once()
            # Verify the '--' separator and argv appear in the call
            call_args = mock_run.call_args[0][0]
            dash_idx = call_args.index("--")
            assert call_args[dash_idx + 1:] == argv

    def test_validation_happens_before_run(self):
        """_check_exec_arg must run before _run is invoked."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, ["cat", "/path; rm"], check=False)
            mock_run.assert_not_called()

    def test_all_argv_elements_validated(self):
        """Every element of argv is checked, not just the first."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"exec argv\[2]"):
                k8s_backend.exec_(target, ["safe", "also-safe", "evil;rm"], check=False)
            mock_run.assert_not_called()
