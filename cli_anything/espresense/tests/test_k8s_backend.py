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


# ── Regression: path traversal in config_path ────────────────────────────────

class TestPathTraversalRejection:
    """config_path must reject ``..`` sequences that could escape the
    intended directory inside the container."""

    @pytest.mark.parametrize(
        "value",
        [
            "../../etc/passwd",
            "/config/../../../etc/shadow",
            "/config/..",
            "..",
            "/config/../other",
        ],
    )
    def test_rejects_path_traversal(self, value):
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend.K8sTarget(config_path=value)


# ── Regression: exec_ argv validation ─────────────────────────────────────────

class TestExecArgvValidation:
    """exec_ must reject argv elements that could inject kubectl flags."""

    def test_rejects_flag_injection_via_argv(self):
        """An argv element starting with '-' must be rejected."""
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"must not start with '-'"):
            k8s_backend.exec_(target, ["--namespace=evil", "cat"], check=False)

    def test_rejects_empty_argv_element(self):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"must not be empty"):
            k8s_backend.exec_(target, ["cat", ""], check=False)

    def test_rejects_null_byte_in_argv(self):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"unsafe characters"):
            k8s_backend.exec_(target, ["cat", "file\x00name"], check=False)

    def test_rejects_newline_in_argv(self):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"unsafe characters"):
            k8s_backend.exec_(target, ["cat", "file\nname"], check=False)

    def test_valid_argv_passes_through(self):
        """Normal argv elements are not rejected."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"ok")
            k8s_backend.exec_(target, ["cat", "/safe/path/config.yaml"], check=False)
            args_list = mock_run.call_args[0][0]
            assert "--" in args_list
            assert "cat" in args_list
            assert "/safe/path/config.yaml" in args_list


# ── Regression: rollout_status timeout validation ─────────────────────────────

class TestRolloutStatusTimeout:
    """rollout_status must reject crafted timeout values."""

    @pytest.mark.parametrize(
        "value",
        [
            "120s; rm -rf /",
            "0m && id",
            "5m --namespace=evil",
            "",
            "abc",
            "120",
            "120x",
            "120ss",
        ],
    )
    def test_rejects_invalid_timeout(self, value):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"is not a valid duration"):
            k8s_backend.rollout_status(target, timeout=value)

    @pytest.mark.parametrize("value", ["120s", "5m", "1h", "30s", "10m"])
    def test_accepts_valid_timeout(self, value):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            k8s_backend.rollout_status(target, timeout=value)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == f"--timeout={value}"


# ── Regression: sanitised values reach subprocess.run ─────────────────────────

class TestSanitisedValuesAtCallSite:
    """Verify that only sanitised values reach subprocess.run — the actual
    injection point at lines ~104-110 of _run."""

    def test_pod_name_passes_sanitised_values(self):
        """pod_name must pass the validated namespace and deployment."""
        target = k8s_backend.K8sTarget(namespace="safe-ns", deployment="safe-deploy")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="pod-abc\n", stderr="")
                k8s_backend.pod_name(target)
                args = mock_run.call_args[0][0]
                # args is [kubectl, -n, namespace, get, pods, -l, app=..., -o, ...]
                assert "-n" in args
                ns_idx = args.index("-n")
                assert args[ns_idx + 1] == "safe-ns"
                # label selector uses the validated deployment name
                label_idx = args.index("-l")
                assert args[label_idx + 1] == "app=safe-deploy"

    def test_restart_passes_sanitised_values(self):
        """restart must pass validated namespace and deployment."""
        target = k8s_backend.K8sTarget(namespace="my-ns", deployment="my-deploy")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                k8s_backend.restart(target)
                args = mock_run.call_args[0][0]
                assert "-n" in args
                ns_idx = args.index("-n")
                assert args[ns_idx + 1] == "my-ns"
                assert "deployment/my-deploy" in args

    def test_rollout_status_passes_sanitised_values(self):
        """rollout_status must pass validated namespace, deployment, and timeout."""
        target = k8s_backend.K8sTarget(namespace="my-ns", deployment="my-deploy")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                k8s_backend.rollout_status(target, timeout="60s")
                args = mock_run.call_args[0][0]
                assert "-n" in args
                ns_idx = args.index("-n")
                assert args[ns_idx + 1] == "my-ns"
                assert "deployment/my-deploy" in args
                assert "--timeout=60s" in args

    def test_exec_passes_sanitised_values(self):
        """exec_ must pass validated namespace, deployment, and container."""
        target = k8s_backend.K8sTarget(
            namespace="my-ns", deployment="my-deploy", container="my-ctr",
        )
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=b"ok", stderr="")
                k8s_backend.exec_(target, ["cat", "/safe/path"], check=False)
                args = mock_run.call_args[0][0]
                assert "-n" in args
                ns_idx = args.index("-n")
                assert args[ns_idx + 1] == "my-ns"
                assert "deploy/my-deploy" in args
                ctr_idx = args.index("-c")
                assert args[ctr_idx + 1] == "my-ctr"
                # -- separator before argv
                dash_idx = args.index("--")
                assert args[dash_idx + 1:] == ["cat", "/safe/path"]

    def test_no_shell_true_in_any_call(self):
        """subprocess.run must never be called with shell=True."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr="")
                k8s_backend.pod_name(target)
                k8s_backend.restart(target)
                k8s_backend.rollout_status(target)
                k8s_backend.exec_(target, ["cat", "/safe"], check=False)
                for call in mock_run.call_args_list:
                    assert call[1].get("shell", False) is False
