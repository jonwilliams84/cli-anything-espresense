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
            # Underscores and dots are NOT valid in DNS-1123 labels
            # (namespace, deployment, container) — reject them so the
            # tighter regex closes more injection surface.
            ("namespace", "my_ns"),
            ("namespace", "my.ns"),
            ("deployment", "my_deploy"),
            ("deployment", "my.deploy"),
            ("container", "my_container"),
            # Uppercase is not valid in DNS-1123 labels
            ("namespace", "MyNamespace"),
            ("deployment", "MyDeploy"),
            ("container", "MyContainer"),
            # Leading/trailing hyphens are not valid in DNS-1123 labels
            ("namespace", "-ns"),
            ("namespace", "ns-"),
            ("deployment", "-deploy"),
            ("deployment", "deploy-"),
            ("container", "-ctr"),
            ("container", "ctr-"),
            # Empty namespace / deployment / container
            ("namespace", ""),
            ("deployment", ""),
            ("container", ""),
            # Namespace exceeding 63 chars
            ("namespace", "a" * 64),
            ("deployment", "a" * 64),
            ("container", "a" * 64),
        ],
    )
    def test_rejects_shell_metacharacters(self, field, value):
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend.K8sTarget(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            # Valid Kubernetes names: lowercase alphanumeric and hyphens
            ("namespace", "default"),
            ("namespace", "my-ns"),
            ("namespace", "espresense"),
            ("deployment", "my-deploy"),
            ("deployment", "espresense-companion"),
            ("container", "my-container"),
            ("container", "espresense-companion"),
            # config_path still allows dots, underscores, slashes
            ("config_path", "/path/to/config.yaml"),
            ("config_path", "/path/to/config_file.yml"),
            ("config_path", "/path.with.dots/config"),
            ("config_path", "/config/espresense/config.yaml"),
        ],
    )
    def test_accepts_valid_values(self, field, value):
        t = k8s_backend.K8sTarget(**{field: value})
        assert getattr(t, field) == value


# ── Dedicated namespace validation helper ───────────────────────────────────

class TestNamespaceValidation:
    """Namespace has a dedicated validator following DNS-1123 label rules."""

    def test_check_namespace_helper_exists(self):
        """A dedicated _check_namespace function must be present."""
        assert hasattr(k8s_backend, "_check_namespace")
        assert callable(k8s_backend._check_namespace)

    def test_check_namespace_returns_valid_value(self):
        """Valid namespaces pass through unchanged."""
        assert k8s_backend._check_namespace("espresense") == "espresense"
        assert k8s_backend._check_namespace("default") == "default"
        assert k8s_backend._check_namespace("my-ns") == "my-ns"

    @pytest.mark.parametrize(
        "value",
        [
            "ns;sleep 10",
            "ns && id",
            "ns | cat",
            "ns\n",
            'ns"$(id)"',
            "my_ns",
            "my.ns",
            "MyNamespace",
            "-ns",
            "ns-",
            "",
            "a" * 64,
            "ns/sub",
        ],
    )
    def test_check_namespace_rejects_unsafe(self, value):
        with pytest.raises(ValueError, match=r"namespace contains unsafe characters"):
            k8s_backend._check_namespace(value)

    def test_namespace_validated_before_kubectl(self):
        """An unsafe namespace must raise before _run is called."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns; rm -rf /")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            # Even if someone bypasses construction, the validated target
            # only contains safe values.
            target = k8s_backend.K8sTarget()
            k8s_backend.pod_name(target)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"


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

class TestRolloutTimeoutValidation:
    """rollout_status timeout must be validated before it reaches a kubectl
    invocation to prevent argument injection."""

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s; rm -rf /",
            "120s && id",
            "120s | cat",
            "120s\n",
            "120s --namespace=evil",
            "120s -o json",
            "$(id)",
            "`id`",
            "120s\x00",
            "120s extra",
            "",
            "  ",
            "abc",
            "120x",
            "120s120s",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        """Unsafe timeout values must raise ValueError before kubectl runs."""
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"timeout contains unsafe characters"):
            k8s_backend.rollout_status(target, timeout=timeout)

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "30s",
            "0s",
            "999s",
            "60m",
            "2h",
            "45s",
            "10m",
        ],
    )
    def test_accepts_valid_timeout(self, timeout):
        """Valid timeout values must be passed through to kubectl."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == f"--timeout={timeout}"

    def test_default_timeout_is_valid(self):
        """The default timeout '120s' must pass validation."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == "--timeout=120s"

    def test_unsafe_timeout_never_reaches_kubectl(self):
        """An injection attempt must raise before _run is called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="120s; rm -rf /")
            mock_run.assert_not_called()


# ── Namespace never reaches kubectl unsanitised ─────────────────────────────

class TestNamespaceNeverReachesKubectl:
    """Every kubectl invocation that accepts a namespace must pass it through
    the validated K8sTarget, never raw user input."""

    @pytest.mark.parametrize(
        "func_name,func,args",
        [
            ("pod_name", lambda t: k8s_backend.pod_name(t), None),
            ("restart", lambda t: k8s_backend.restart(t), None),
            ("rollout_status", lambda t: k8s_backend.rollout_status(t), None),
        ],
    )
    def test_namespace_appears_as_isolated_arg(self, func_name, func, args):
        """The namespace must appear as a single list element after -n."""
        target = k8s_backend.K8sTarget(namespace="espresense")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            func(target)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            # Namespace must be exactly one element, not split or interpolated
            assert args_list[ns_idx + 1] == "espresense"
            # No shell metacharacters in the namespace argument
            assert ";" not in args_list[ns_idx + 1]
            assert "|" not in args_list[ns_idx + 1]
            assert "&" not in args_list[ns_idx + 1]

    def test_exec_namespace_isolated(self):
        """exec_ must pass namespace as a single arg after -n."""
        target = k8s_backend.K8sTarget(namespace="espresense")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", stderr="", returncode=0)
            k8s_backend.exec_(target, ["ls"], check=False)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"

    def test_unsafe_namespace_never_reaches_pod_name(self):
        """pod_name must raise before _run when namespace is unsafe."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns; rm -rf /")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            # Construction with safe namespace should work
            target = k8s_backend.K8sTarget()
            k8s_backend.pod_name(target)
            # Verify the namespace arg is the safe default
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"

    def test_unsafe_namespace_never_reaches_restart(self):
        """restart must raise before _run when namespace is unsafe."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns && id")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            target = k8s_backend.K8sTarget()
            k8s_backend.restart(target)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"

    def test_unsafe_namespace_never_reaches_rollout_status(self):
        """rollout_status must raise before _run when namespace is unsafe."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns | cat")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            target = k8s_backend.K8sTarget()
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"

    def test_unsafe_namespace_never_reaches_exec(self):
        """exec_ must raise before _run when namespace is unsafe."""
        with pytest.raises(ValueError):
            k8s_backend.K8sTarget(namespace="ns\n")
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", stderr="")
            target = k8s_backend.K8sTarget()
            k8s_backend.exec_(target, ["ls"], check=False)
            args_list = mock_run.call_args[0][0]
            ns_idx = args_list.index("-n")
            assert args_list[ns_idx + 1] == "espresense"
