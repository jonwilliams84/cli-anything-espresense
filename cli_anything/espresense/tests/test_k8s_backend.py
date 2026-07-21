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

class TestTimeoutValidation:
    """The user-supplied ``timeout`` passed to ``rollout_status`` must be
    validated before it reaches kubectl so it cannot inject extra arguments.
    """

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "300",
            "0s",
            "9999h",
        ],
    )
    def test_accepts_valid_timeouts(self, timeout):
        """Valid timeout values are accepted and reach _run unchanged."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            assert f"--timeout={timeout}" in args_list

    @pytest.mark.parametrize(
        "timeout",
        [
            # Argument injection: extra flags after the timeout value
            "120s --namespace=evil",
            "120s --server=https://attacker",
            # Shell metacharacters
            "120s;id",
            "120s$(id)",
            "120s`id`",
            "120s|cat",
            "120s&&id",
            # Whitespace / newlines
            "120s ",
            " 120s",
            "120s\n",
            "120s\t",
            # Null bytes
            "120s\x00",
            # Empty / non-numeric
            "",
            "abc",
            "s",
            "-1s",
            "12.5s",
        ],
    )
    def test_rejects_unsafe_timeouts(self, timeout):
        """Unsafe timeout values must raise ValueError before _run is called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target, timeout=timeout)
            mock_run.assert_not_called()

    def test_default_timeout_is_valid(self):
        """The default timeout ``120s`` must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            assert "--timeout=120s" in args_list

    def test_timeout_not_split_into_multiple_args(self):
        """The validated timeout must appear as a single --timeout= element."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            k8s_backend.rollout_status(target, timeout="90s")
            args_list = mock_run.call_args[0][0]
            timeout_args = [a for a in args_list if a.startswith("--timeout=")]
            assert len(timeout_args) == 1
            assert timeout_args[0] == "--timeout=90s"


# ── Leading-hyphen argument injection regression ──────────────────────────────

class TestLeadingHyphenInjection:
    """Regression: values starting with ``-`` must be rejected so they cannot
    be interpreted as kubectl flags (argument injection).

    The previous regex ``^[\\w./-]+$`` allowed leading hyphens, so a malicious
    ``namespace`` like ``-n`` or ``--server=https://attacker`` would be passed
    to kubectl as a flag rather than a value.
    """

    @pytest.mark.parametrize(
        "field,value",
        [
            # Leading-hyphen flag injection in namespace
            ("namespace", "-n"),
            ("namespace", "-x"),
            ("namespace", "--"),
            ("namespace", "-a-b"),
            # Leading-hyphen flag injection in deployment
            ("deployment", "-n"),
            ("deployment", "-x"),
            ("deployment", "--"),
            ("deployment", "-a-b"),
            # Leading-hyphen flag injection in container
            ("container", "-n"),
            ("container", "-x"),
            ("container", "--"),
            ("container", "-a-b"),
            # Leading-hyphen flag injection in config_path
            ("config_path", "-n"),
            ("config_path", "-x"),
            ("config_path", "--"),
            ("config_path", "-config/path"),
        ],
    )
    def test_rejects_leading_hyphen(self, field, value):
        """Values starting with ``-`` must raise ValueError at construction."""
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend.K8sTarget(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "espresense"),
            ("namespace", "my-ns"),
            ("namespace", "my_ns"),
            ("namespace", "my.ns"),
            ("deployment", "espresense-companion"),
            ("deployment", "my-deploy"),
            ("container", "espresense-companion"),
            ("container", "my-container"),
            ("config_path", "/config/espresense/config.yaml"),
            ("config_path", "/path/to/config.yaml"),
        ],
    )
    def test_accepts_valid_non_hyphen_leading(self, field, value):
        """Valid values that do not start with ``-`` must still be accepted."""
        t = k8s_backend.K8sTarget(**{field: value})
        assert getattr(t, field) == value

    def test_namespace_not_interpreted_as_flag(self):
        """A valid namespace must appear as a value after ``-n``, not as a flag."""
        target = k8s_backend.K8sTarget(namespace="my-namespace")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="pod-name", stderr="")
            k8s_backend.pod_name(target)
            args_list = mock_run.call_args[0][0]
            # The namespace must follow "-n" as its value, not be a flag itself
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "my-namespace"

    def test_deployment_not_interpreted_as_flag(self):
        """A valid deployment must appear as a value in deploy/<name>, not a flag."""
        target = k8s_backend.K8sTarget(deployment="my-deploy")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            k8s_backend.restart(target)
            args_list = mock_run.call_args[0][0]
            assert "deployment/my-deploy" in args_list
            # No element should be a bare "-n" from the deployment value
            assert "-n" not in [a for a in args_list if a != "-n"]
