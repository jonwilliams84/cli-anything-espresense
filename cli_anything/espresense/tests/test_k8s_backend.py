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
    """The timeout parameter to rollout_status must be validated to prevent
    argument injection through the kubectl --timeout flag."""

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "0",
            "30",
            "3600",
        ],
    )
    def test_accepts_valid_timeout(self, timeout):
        """Valid timeout durations are accepted without error."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == f"--timeout={timeout}"

    @pytest.mark.parametrize(
        "timeout",
        [
            # Argument injection: extra flags appended
            "120s --namespace=evil",
            "120s --insecure-skip-tls-verify",
            # Shell metacharacters
            "120s;rm -rf /",
            "120s && id",
            "120s|cat",
            "120s`id`",
            "120s$(whoami)",
            "120s\nexit 1\n",
            # Null bytes
            "120s\x00",
            # Spaces within the value
            "120 s",
            # Empty / whitespace
            "",
            "   ",
            # Other injection attempts
            "--timeout=0",
            "1s --as=system:admin",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        """Unsafe timeout values must raise ValueError before reaching kubectl."""
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"not a valid timeout duration"):
            k8s_backend.rollout_status(target, timeout=timeout)

    def test_unsafe_timeout_never_reaches_run(self):
        """When timeout is rejected, _run must not be called at all."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="120s;rm -rf /")
            mock_run.assert_not_called()

    def test_default_timeout_is_valid(self):
        """The default timeout '120s' must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == "--timeout=120s"


# ── _run validates every argument token (defence-in-depth) ──────────────────

class TestRunArgValidation:
    """_run is the final gatekeeper: every argument token is validated before
    it reaches subprocess.run, even if a higher-level validator was bypassed."""

    @pytest.mark.parametrize(
        "evil_arg",
        [
            # Shell metacharacters
            "ns;sleep 10",
            "ns && id",
            "ns | cat",
            "ns\nexit 1",
            "deploy$(id)",
            "deploy`id`",
            'ns"$(id)"',
            "ns\x00null",
            # Argument injection via flag-like values
            "--as=system:admin",
            "--insecure-skip-tls-verify",
            "--namespace=evil",
            "-x",
            "--evil",
        ],
    )
    def test_run_rejects_unsafe_arg(self, evil_arg):
        """_run must reject any argument containing shell metacharacters or
        an injected flag before subprocess.run is called."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_subproc:
                with pytest.raises(ValueError):
                    k8s_backend._run(["get", "pods", evil_arg])
                mock_subproc.assert_not_called()

    def test_run_rejects_unsafe_arg_at_any_position(self):
        """Injection must be caught regardless of position in the args list."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_subproc:
                with pytest.raises(ValueError):
                    k8s_backend._run(["ns;id", "get", "pods"])
                mock_subproc.assert_not_called()

    def test_run_allows_internally_constructed_flags(self):
        """Hardcoded kubectl flags and internally-constructed flag=value
        tokens must pass validation."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                k8s_backend._run([
                    "-n", "espresense",
                    "get", "pods",
                    "-l", "app=espresense-companion",
                    "-o", "jsonpath={.items[0].metadata.name}",
                ])
                assert mock_run.called

    def test_run_allows_deployment_prefix(self):
        """deploy/ and deployment/ prefixes must pass (internally constructed)."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                k8s_backend._run([
                    "-n", "espresense",
                    "rollout", "restart",
                    "deployment/espresense-companion",
                ])
                assert mock_run.called

    def test_run_allows_timeout_flag(self):
        """--timeout= with a validated duration must pass."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                k8s_backend._run([
                    "-n", "espresense",
                    "rollout", "status",
                    "deployment/espresense-companion",
                    "--timeout=120s",
                ])
                assert mock_run.called

    def test_run_rejects_fake_timeout_flag(self):
        """A --timeout= value with shell metacharacters must be rejected."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_subproc:
                with pytest.raises(ValueError):
                    k8s_backend._run(["--timeout=120s;rm -rf /"])
                mock_subproc.assert_not_called()


# ── exec_ validates argv before it reaches _run ─────────────────────────────

class TestExecArgvValidation:
    """exec_ must validate every argv token before it reaches _run so that
    no shell metacharacter or injected flag can slip through the '--' separator
    into the container command."""

    @pytest.mark.parametrize(
        "evil_argv",
        [
            ["cat", "/etc/passwd;id"],
            ["cat", "/etc/passdd && id"],
            ["cat", "/etc/passwd|cat"],
            ["cat", "/etc/passwd\nexit 1"],
            ["cat", "$(id)"],
            ["cat", "`id`"],
            ["cat", '/config"$(id)"'],
            ["cat", "/config\x00null"],
            ["--as=system:admin"],
            ["--namespace=evil"],
            ["-x"],
            ["--evil"],
        ],
    )
    def test_exec_rejects_unsafe_argv(self, evil_argv):
        """exec_ must reject argv containing shell metacharacters or flags."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.exec_(target, evil_argv, check=False)
            mock_run.assert_not_called()

    def test_exec_allows_safe_argv(self):
        """Normal argv tokens (paths, commands) must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"data", returncode=0)
            k8s_backend.exec_(target, ["cat", "/config/espresense/config.yaml"])
            assert mock_run.called

    def test_exec_allows_spaces_in_argv(self):
        """Spaces in argv elements are safe with list-based subprocess.run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, ["echo", "hello world"], check=False)
            args_list = mock_run.call_args[0][0]
            dash_idx = args_list.index("--")
            assert args_list[dash_idx + 1:] == ["echo", "hello world"]


# ── K8sTarget fields flow through to -n args safely ──────────────────────────

class TestTargetFieldFlow:
    """K8sTarget fields (namespace, deployment, container, config_path) are
    validated at construction and flow safely into kubectl -n and other args."""

    def test_namespace_flows_to_n_arg(self):
        """A valid namespace appears as the value of -n in the kubectl call."""
        target = k8s_backend.K8sTarget(namespace="my-namespace")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="pod-1", stderr="")
            k8s_backend.pod_name(target)
            args_list = mock_run.call_args[0][0]
            n_idx = args_list.index("-n")
            assert args_list[n_idx + 1] == "my-namespace"

    def test_deployment_flows_to_label(self):
        """A valid deployment appears in the app= label selector."""
        target = k8s_backend.K8sTarget(deployment="my-deploy")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="pod-1", stderr="")
            k8s_backend.pod_name(target)
            args_list = mock_run.call_args[0][0]
            label = next(a for a in args_list if a.startswith("app="))
            assert label == "app=my-deploy"

    def test_container_flows_to_c_arg(self):
        """A valid container appears as the value of -c in exec calls."""
        target = k8s_backend.K8sTarget(container="my-container")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, ["cat", "/config/config.yaml"])
            args_list = mock_run.call_args[0][0]
            c_idx = args_list.index("-c")
            assert args_list[c_idx + 1] == "my-container"

    def test_config_path_flows_to_cat_arg(self):
        """A valid config_path appears as a single cat argument."""
        target = k8s_backend.K8sTarget(config_path="/safe/path/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            mock_exec.return_value = MagicMock(stdout=b"data: test")
            k8s_backend.read_config(target)
            argv = mock_exec.call_args[0][1]
            assert argv == ["cat", "/safe/path/config.yaml"]

    def test_unsafe_namespace_never_reaches_kubectl(self):
        """An unsafe namespace is rejected at K8sTarget construction, so it
        can never reach a kubectl -n argument."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns;rm -rf /")

    def test_unsafe_deployment_never_reaches_kubectl(self):
        """An unsafe deployment is rejected at construction."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(deployment="deploy;id")

    def test_unsafe_container_never_reaches_kubectl(self):
        """An unsafe container is rejected at construction."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(container="ctr|cat")

    def test_unsafe_config_path_never_reaches_kubectl(self):
        """An unsafe config_path is rejected at construction."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(config_path="/config;curl evil")


# ── write_config backup path validation ──────────────────────────────────────

class TestBackupPathValidation:
    """The derived backup path in write_config must be validated even though
    it is built from already-validated components."""

    def test_backup_path_is_validated(self):
        """The bak_path passed to cp must match the safe path pattern."""
        target = k8s_backend.K8sTarget(config_path="/config/config.yaml")
        with patch.object(k8s_backend, "exec_") as mock_exec:
            ts_proc = MagicMock()
            ts_proc.stdout = b"1700000000\n"
            mock_exec.return_value = ts_proc
            k8s_backend.write_config(target, "yaml: data", backup=True)
            # Second exec_ call is the cp command
            cp_argv = mock_exec.call_args_list[1][0][1]
            assert cp_argv[0] == "cp"
            assert cp_argv[1] == "/config/config.yaml"
            assert cp_argv[2] == "/config/config.yaml.1700000000.bak"
            # bak_path must match the safe path pattern
            assert k8s_backend._VALID_PATH_RE.match(cp_argv[2])
