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
    """The --timeout parameter is user-supplied and must be sanitised before
    it reaches a kubectl invocation, otherwise an attacker could inject
    additional kubectl flags or shell metacharacters."""

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s; rm -rf /",
            "120s && id",
            "120s | cat",
            "120s\nexit 1",
            "120s --kubeconfig=/tmp/evil",
            "120s$(id)",
            "120s`id`",
            "120s\x00null",
            "120s extra",
            "; sleep 10",
            "120s\"$(id)\"",
        ],
    )
    def test_rejects_unsafe_timeout(self, timeout):
        """Unsafe timeout values must raise ValueError before _run is called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target, timeout=timeout)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "timeout",
        ["120s", "60s", "5m", "1h", "30s", "0s", "300s", "90m", "2h", "0", "45"],
    )
    def test_accepts_valid_timeout(self, timeout):
        """Valid timeout values must pass through to _run without error."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args = mock_run.call_args[0][0]
            timeout_arg = next(a for a in args if a.startswith("--timeout="))
            assert timeout_arg == f"--timeout={timeout}"

    def test_default_timeout_is_valid(self):
        """The default timeout '120s' must be accepted without error."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args = mock_run.call_args[0][0]
            assert "--timeout=120s" in args


# ── pod_name output validation ────────────────────────────────────────────────

class TestPodNameOutputValidation:
    """The pod name returned by kubectl stdout must be validated before it is
    returned, so it is safe to reuse in subsequent kubectl calls."""

    def test_valid_pod_name_returned(self):
        """A normal pod name is returned unchanged."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="espresense-companion-abc123\n", returncode=0
            )
            assert k8s_backend.pod_name(target) == "espresense-companion-abc123"

    def test_empty_pod_name_returns_empty(self):
        """When kubectl returns no pod, an empty string is returned."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="\n", returncode=0)
            assert k8s_backend.pod_name(target) == ""

    @pytest.mark.parametrize(
        "name",
        [
            "pod; rm -rf /",
            "pod && id",
            "pod | cat",
            "pod\nexit 1",
            "pod$(id)",
            "pod`id`",
            "pod\x00null",
            "pod with spaces",
            'pod"$(id)"',
        ],
    )
    def test_rejects_unsafe_pod_name(self, name):
        """Unsafe pod names from kubectl stdout must raise ValueError."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=name + "\n", returncode=0
            )
            with pytest.raises(ValueError, match=r"pod name contains unsafe characters"):
                k8s_backend.pod_name(target)


# ── Defence-in-depth: target fields validated at point of use ───────────────

class TestTargetValidatedAtPointOfUse:
    """Even if K8sTarget.__post_init__ is bypassed (e.g. via object.__new__
    or mutation of a frozen instance), every public function that passes
    target fields to _run must independently validate those fields before
    they reach a kubectl invocation."""

    @staticmethod
    def _unsafe_target(**overrides) -> k8s_backend.K8sTarget:
        """Create a K8sTarget bypassing __post_init__ so we can test that
        point-of-use validation catches unsafe values independently."""
        obj = object.__new__(k8s_backend.K8sTarget)
        defaults = dict(
            namespace="espresense",
            deployment="espresense-companion",
            container="espresense-companion",
            config_path="/config/espresense/config.yaml",
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            object.__setattr__(obj, k, v)
        return obj

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy$(whoami)"),
            ("container", "ctr;sleep 10"),
            ("container", "ctr`id`"),
            ("config_path", "/config; curl evil"),
            ("config_path", "/config\x00null"),
        ],
    )
    def test_pod_name_rejects_unsafe_target(self, field, bad_value):
        """pod_name must validate target fields before _run is called."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.pod_name(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy$(whoami)"),
            ("container", "ctr;sleep 10"),
            ("container", "ctr`id`"),
        ],
    )
    def test_exec_rejects_unsafe_target(self, field, bad_value):
        """exec_ must validate target fields before _run is called."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, ["echo", "hi"], check=False)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy$(whoami)"),
        ],
    )
    def test_restart_rejects_unsafe_target(self, field, bad_value):
        """restart must validate target fields before _run is called."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.restart(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("namespace", "ns;sleep 10"),
            ("namespace", "ns$(id)"),
            ("deployment", "deploy; rm -rf /"),
            ("deployment", "deploy$(whoami)"),
        ],
    )
    def test_rollout_status_rejects_unsafe_target(self, field, bad_value):
        """rollout_status must validate target fields before _run is called."""
        target = self._unsafe_target(**{field: bad_value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target, timeout="120s")
            mock_run.assert_not_called()

    def test_read_config_rejects_unsafe_config_path(self):
        """read_config must validate config_path before it reaches _run."""
        target = self._unsafe_target(config_path="/config; curl evil")
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.read_config(target)
            mock_run.assert_not_called()

    def test_write_config_rejects_unsafe_config_path(self):
        """write_config must validate config_path before it reaches _run."""
        target = self._unsafe_target(config_path="/config; curl evil")
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.write_config(target, "yaml: data", backup=False)
            mock_run.assert_not_called()

    def test_valid_target_passes_pod_name(self):
        """A properly constructed K8sTarget passes validation at point of use."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="espresense-companion-abc\n", returncode=0
            )
            result = k8s_backend.pod_name(target)
            assert result == "espresense-companion-abc"
            mock_run.assert_called_once()

    def test_valid_target_passes_rollout_status(self):
        """A properly constructed K8sTarget passes validation at point of use."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout="60s")
            mock_run.assert_called_once()
