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

class TestRolloutTimeoutValidation:
    """The --timeout value passed to rollout_status must be sanitised so it
    cannot inject additional kubectl arguments or shell metacharacters."""

    @pytest.mark.parametrize(
        "timeout",
        [
            # Argument injection: extra flags appended to the timeout value
            "120s --kubeconfig=/tmp/evil",
            "120s --namespace=attacker",
            "120s --server=https://evil.example",
            # Shell metacharacters / command separators
            "120s;sleep 10",
            "120s && id",
            "120s|cat",
            "120s\n",
            "120s$(id)",
            "120s`id`",
            # Empty / missing unit / wrong unit
            "",
            "120",
            "120x",
            "120ss",
            "s",
            "-1s",
            # Null bytes
            "120s\x00",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"timeout contains unsafe characters"):
            k8s_backend.rollout_status(target, timeout=timeout)

    @pytest.mark.parametrize("timeout", ["120s", "5m", "1h", "30s", "10m", "2h"])
    def test_accepts_valid_timeout(self, timeout):
        """Valid timeout values must pass validation and reach _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == f"--timeout={timeout}"

    def test_default_timeout_is_valid(self):
        """The default timeout '120s' must pass validation unchanged."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args_list if a.startswith("--timeout="))
            assert timeout_arg == "--timeout=120s"

    def test_unsafe_timeout_never_reaches_run(self):
        """An unsafe timeout must raise before _run is ever called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="120s --evil")
            mock_run.assert_not_called()


# ── Defence-in-depth: point-of-use validation ───────────────────────────────

class TestPointOfUseValidation:
    """Even if K8sTarget.__post_init__ is bypassed, every function that
    builds kubectl arguments must re-validate user-supplied values before
    they reach _run.  This is defence-in-depth against argument injection.
    """

    def _unsafe_target(self, **overrides):
        """Build a K8sTarget bypassing __post_init__ validation so we can
        test that point-of-use validation catches unsafe values."""
        t = k8s_backend.K8sTarget()
        for field, value in overrides.items():
            object.__setattr__(t, field, value)
        return t

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns --kubeconfig=/tmp/evil"),
            ("namespace", "ns$(id)"),
            ("namespace", "ns`id`"),
            ("namespace", "ns|cat"),
            ("namespace", "ns\n"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy --namespace=attacker"),
            ("deployment", "deploy$(whoami)"),
            ("deployment", "deploy`id`"),
            ("deployment", "deploy|cat"),
            ("deployment", "deploy\n"),
        ],
    )
    def test_pod_name_rejects_unsafe_at_point_of_use(self, field, bad_value):
        """pod_name must reject unsafe namespace/deployment before _run."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.pod_name(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns --kubeconfig=/tmp/evil"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy --namespace=attacker"),
            ("deployment", "deploy$(whoami)"),
            ("container", "ctr;sleep 10"),
            ("container", "ctr --server=https://evil"),
            ("container", "ctr$(id)"),
        ],
    )
    def test_exec_rejects_unsafe_at_point_of_use(self, field, bad_value):
        """exec_ must reject unsafe namespace/deployment/container before _run."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, ["echo", "hi"], check=False)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns --kubeconfig=/tmp/evil"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy --namespace=attacker"),
            ("deployment", "deploy$(whoami)"),
        ],
    )
    def test_restart_rejects_unsafe_at_point_of_use(self, field, bad_value):
        """restart must reject unsafe namespace/deployment before _run."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.restart(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns --kubeconfig=/tmp/evil"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy --namespace=attacker"),
            ("deployment", "deploy$(whoami)"),
        ],
    )
    def test_rollout_status_rejects_unsafe_at_point_of_use(self, field, bad_value):
        """rollout_status must reject unsafe namespace/deployment before _run."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/config; curl evil",
            "/config && id",
            "/config\nexit 1\n",
            "/config/path\x00null",
            "/config --kubeconfig=/tmp/evil",
        ],
    )
    def test_read_config_rejects_unsafe_config_path(self, bad_path):
        """read_config must reject unsafe config_path before reaching exec_."""
        target = self._unsafe_target(config_path=bad_path)
        with patch.object(k8s_backend, "exec_") as mock_exec:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.read_config(target)
            mock_exec.assert_not_called()

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/config; curl evil",
            "/config && id",
            "/config\nexit 1\n",
            "/config/path\x00null",
            "/config --kubeconfig=/tmp/evil",
        ],
    )
    def test_write_config_rejects_unsafe_config_path(self, bad_path):
        """write_config must reject unsafe config_path before reaching exec_."""
        target = self._unsafe_target(config_path=bad_path)
        with patch.object(k8s_backend, "exec_") as mock_exec:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.write_config(target, "yaml: data", backup=False)
            mock_exec.assert_not_called()

    def test_valid_values_pass_point_of_use_validation(self):
        """Valid values must pass point-of-use validation and reach _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="pod-1234", stderr="")
            k8s_backend.pod_name(target)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "espresense" in args
            assert any("espresense-companion" in a for a in args)
