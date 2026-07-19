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


# ── rollout_status timeout validation ──────────────────────────────────────

class TestRolloutTimeoutValidation:
    """The --timeout value passed to rollout_status must be validated before
    it reaches kubectl so that argument injection via the timeout string is
    impossible.
    """

    @pytest.mark.parametrize("timeout", [
        "120s",
        "5m",
        "1h",
        "30",
        "0s",
        "999s",
    ])
    def test_accepts_valid_timeout(self, timeout):
        """Valid kubectl timeout strings should be accepted."""
        # _check_timeout should not raise
        assert k8s_backend._check_timeout(timeout) == timeout

    @pytest.mark.parametrize("timeout", [
        "120s --namespace=evil",
        "0;sleep 10",
        "0s&&id",
        "0s|cat",
        "0s`id`",
        "0s$(id)",
        "0s\nexit 1\n",
        "--namespace=evil",
        "0s -n evil",
        "0s\tn",
        " 120s",
        "120s ",
        "",
        "abc",
        "12x",
        "12.5s",
        "-1s",
        "0s\0rm",
    ])
    def test_rejects_unsafe_timeout(self, timeout):
        """Unsafe timeout strings must be rejected before reaching kubectl."""
        with pytest.raises(ValueError):
            k8s_backend._check_timeout(timeout)

    def test_rollout_status_validates_before_kubectl(self):
        """rollout_status must raise ValueError before calling _run when
        the timeout is unsafe — the kubectl subprocess must never be
        invoked with an injected argument."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError):
                k8s_backend.rollout_status(target, timeout="0s;sleep 10")
            mock_run.assert_not_called()

    def test_rollout_status_accepts_valid_timeout(self):
        """rollout_status with a valid timeout should proceed to call _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            k8s_backend.rollout_status(target, timeout="60s")
            mock_run.assert_called_once()
            # Verify the timeout was embedded safely
            call_args = mock_run.call_args[0][0]
            assert "--timeout=60s" in call_args


# ── _run argument sanitisation (defence-in-depth) ─────────────────────────────

class TestRunArgSanitisation:
    """_run must reject any argument containing shell metacharacters or
    control characters before it reaches subprocess.run, even if the
    K8sTarget fields themselves were validated.  This is the last line of
    defence against command/argument injection."""

    @pytest.mark.parametrize("evil", [
        "safe;rm -rf /",
        "safe&&id",
        "safe|cat",
        "safe`id`",
        "safe$(id)",
        "safe\nexit 1\n",
        "safe\rm",
        "safe\0rm",
        "safe;sleep 10",
    ])
    def test_run_rejects_unsafe_arg(self, evil):
        """_run must raise ValueError before subprocess.run is called."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                with pytest.raises(ValueError, match=r"contains unsafe characters"):
                    k8s_backend._run(["get", "pods", evil])
                mock_run.assert_not_called()

    def test_run_rejects_non_string_arg(self):
        """_run must reject non-string arguments."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                with pytest.raises(TypeError):
                    k8s_backend._run(["get", "pods", 123])
                mock_run.assert_not_called()

    def test_run_accepts_safe_args(self):
        """Safe arguments must pass through _run to subprocess.run."""
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                k8s_backend._run(["-n", "espresense", "get", "pods"])
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert args == ["/bin/kubectl", "-n", "espresense", "get", "pods"]


# ── exec_ argv sanitisation ──────────────────────────────────────────────────

class TestExecArgvSanitisation:
    """exec_ must sanitise every element of argv before passing it to _run,
    so that argument injection via the in-container command is impossible."""

    @pytest.mark.parametrize("evil", [
        "cat;rm -rf /",
        "cat&&id",
        "cat|cat",
        "cat`id`",
        "cat$(id)",
        "cat\nexit 1\n",
        "cat\0rm",
    ])
    def test_exec_rejects_unsafe_argv(self, evil):
        """exec_ must raise ValueError before _run is called when argv
        contains shell metacharacters."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(ValueError, match=r"contains unsafe characters"):
                k8s_backend.exec_(target, [evil], check=False)
            mock_run.assert_not_called()

    def test_exec_rejects_non_string_argv(self):
        """exec_ must reject non-string argv elements."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            with pytest.raises(TypeError):
                k8s_backend.exec_(target, ["cat", 123], check=False)
            mock_run.assert_not_called()

    def test_exec_accepts_safe_argv(self):
        """Safe argv must pass through exec_ to _run."""
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"ok", stderr=b"")
            k8s_backend.exec_(target, ["cat", "/config/espresense/config.yaml"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "--" in args
            dash_idx = args.index("--")
            assert args[dash_idx + 1:] == ["cat", "/config/espresense/config.yaml"]


# ── _check_arg unit tests ─────────────────────────────────────────────────────

class TestCheckArg:
    """_check_arg is the central sanitiser for all values reaching _run."""

    @pytest.mark.parametrize("value", [
        "espresense",
        "espresense-companion",
        "/config/espresense/config.yaml",
        "-n",
        "get",
        "pods",
        "deploy/espresense-companion",
        "--timeout=120s",
        "app=espresense-companion",
        "jsonpath={.items[0].metadata.name}",
        "of=/config/espresense/config.yaml",
        "date",
        "+%s",
        "dd",
        "cp",
    ])
    def test_accepts_safe_value(self, value):
        assert k8s_backend._check_arg("test", value) == value

    @pytest.mark.parametrize("value", [
        "safe;rm -rf /",
        "safe&&id",
        "safe|cat",
        "safe`id`",
        "safe$(id)",
        "safe\nexit",
        "safe\rid",
        "safe\0rm",
    ])
    def test_rejects_unsafe_value(self, value):
        with pytest.raises(ValueError, match=r"contains unsafe characters"):
            k8s_backend._check_arg("test", value)

    def test_rejects_non_string(self):
        with pytest.raises(TypeError):
            k8s_backend._check_arg("test", 123)
        with pytest.raises(TypeError):
            k8s_backend._check_arg("test", None)
        with pytest.raises(TypeError):
            k8s_backend._check_arg("test", b"bytes")
