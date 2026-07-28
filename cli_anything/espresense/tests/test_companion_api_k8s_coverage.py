"""Tests for uncovered behaviour in companion_api, k8s_backend, and companion_client.

These target real logic that the existing suite never exercises:
- companion_api: URL construction, boolean param serialization, path interpolation
- k8s_backend: _kubectl error path, _run failure path, pod_name fallback,
  exec_ stdin flag, restart, write_config backup logic
- companion_client: post/put/delete convenience methods and _parse fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cli_anything.espresense.core import companion_api, k8s_backend
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError


# ── companion_api: URL construction and param serialization ──────────────────


class TestCompanionApiWrappers:
    """Each wrapper must call the right endpoint with correctly serialized params."""

    def test_get_config_hits_state_config(self):
        client = MagicMock()
        client.get.return_value = {"key": "val"}
        result = companion_api.get_config(client)
        path = client.get.call_args[0][0]
        assert path == "/api/state/config"
        assert result == {"key": "val"}

    def test_list_nodes_serializes_telemetry_flag_true(self):
        client = MagicMock()
        companion_api.list_nodes(client, include_telemetry=True)
        params = client.get.call_args[1].get("params")
        assert params == {"includeTele": "true"}

    def test_list_nodes_serializes_telemetry_flag_false(self):
        client = MagicMock()
        companion_api.list_nodes(client, include_telemetry=False)
        params = client.get.call_args[1].get("params")
        assert params == {"includeTele": "false"}

    def test_list_devices_serializes_show_all_flag(self):
        client = MagicMock()
        companion_api.list_devices(client, show_all=True)
        params = client.get.call_args[1].get("params")
        assert params == {"showAll": "true"}

    def test_get_locator_state(self):
        client = MagicMock()
        companion_api.get_locator_state(client)
        assert client.get.call_args[0][0] == "/api/state/locator"

    def test_get_calibration(self):
        client = MagicMock()
        companion_api.get_calibration(client)
        assert client.get.call_args[0][0] == "/api/state/calibration"

    def test_reset_calibration_uses_post(self):
        client = MagicMock()
        companion_api.reset_calibration(client)
        assert client.post.call_args[0][0] == "/api/state/calibration/reset"

    def test_get_auto_optimize(self):
        client = MagicMock()
        companion_api.get_auto_optimize(client)
        assert client.get.call_args[0][0] == "/api/state/calibration/auto-optimize"

    def test_set_auto_optimize_sends_bool_json(self):
        client = MagicMock()
        companion_api.set_auto_optimize(client, True)
        assert client.post.call_args[1].get("json") is True

    def test_set_auto_optimize_false_sends_false(self):
        client = MagicMock()
        companion_api.set_auto_optimize(client, False)
        assert client.post.call_args[1].get("json") is False

    def test_get_node_interpolates_id(self):
        client = MagicMock()
        companion_api.get_node(client, "node-42")
        assert client.get.call_args[0][0] == "/api/node/node-42"

    def test_put_node_sends_settings_json(self):
        client = MagicMock()
        companion_api.put_node(client, "n1", {"room": "kitchen"})
        assert client.put.call_args[0][0] == "/api/node/n1"
        assert client.put.call_args[1].get("json") == {"room": "kitchen"}

    def test_restart_node_uses_post(self):
        client = MagicMock()
        companion_api.restart_node(client, "n1")
        assert client.post.call_args[0][0] == "/api/node/n1/restart"

    def test_update_node_firmware_sends_url(self):
        client = MagicMock()
        companion_api.update_node_firmware(client, "n1", "http://fw/ota.bin")
        assert client.post.call_args[0][0] == "/api/node/n1/update"
        assert client.post.call_args[1].get("json") == {"url": "http://fw/ota.bin"}

    def test_delete_node_uses_delete(self):
        client = MagicMock()
        companion_api.delete_node(client, "n1")
        assert client.delete.call_args[0][0] == "/api/node/n1"

    def test_get_device_interpolates_id(self):
        client = MagicMock()
        companion_api.get_device(client, "dev-99")
        assert client.get.call_args[0][0] == "/api/device/dev-99"

    def test_put_device_sends_settings_json(self):
        client = MagicMock()
        companion_api.put_device(client, "d1", {"name": "phone"})
        assert client.put.call_args[0][0] == "/api/device/d1"
        assert client.put.call_args[1].get("json") == {"name": "phone"}

    def test_delete_device_uses_delete(self):
        client = MagicMock()
        companion_api.delete_device(client, "d1")
        assert client.delete.call_args[0][0] == "/api/device/d1"

    def test_get_device_history_with_start_and_end(self):
        client = MagicMock()
        companion_api.get_device_history(client, "d1", start="2024-01-01", end="2024-02-01")
        assert client.get.call_args[0][0] == "/api/history/d1/range"
        params = client.get.call_args[1].get("params")
        assert params == {"start": "2024-01-01", "end": "2024-02-01"}

    def test_get_device_history_start_only(self):
        client = MagicMock()
        companion_api.get_device_history(client, "d1", start="2024-01-01")
        params = client.get.call_args[1].get("params")
        assert params == {"start": "2024-01-01"}

    def test_list_firmware_types(self):
        client = MagicMock()
        companion_api.list_firmware_types(client)
        assert client.get.call_args[0][0] == "/api/firmware/types"


# ── k8s_backend: _kubectl, _run, pod_name, exec_, restart, write_config ──────


class TestKubectlNotFound:
    def test_kubectl_missing_raises_runtime_error(self):
        with patch.object(k8s_backend.shutil, "which", return_value=None):
            with pytest.raises(RuntimeError, match="kubectl not found"):
                k8s_backend._kubectl()


class TestRunFailurePath:
    def test_run_raises_on_nonzero_exit_when_check_true(self):
        mock_proc = MagicMock(returncode=1, stdout="ok", stderr="boom")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="failed .exit 1."):
                    k8s_backend._run(["get", "pods"], check=True)

    def test_run_returns_proc_on_nonzero_exit_when_check_false(self):
        mock_proc = MagicMock(returncode=1, stdout="", stderr="err")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run", return_value=mock_proc):
                proc = k8s_backend._run(["get", "pods"], check=False)
                assert proc.returncode == 1

    def test_run_strips_stderr_in_error_message(self):
        mock_proc = MagicMock(returncode=2, stdout="", stderr="  error msg  \n")
        with patch.object(k8s_backend, "_kubectl", return_value="/bin/kubectl"):
            with patch.object(k8s_backend.subprocess, "run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="error msg"):
                    k8s_backend._run(["get", "pods"])


class TestPodName:
    def test_pod_name_returns_stripped_stdout(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout="pod-abc-123\n", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc) as mock_run:
            name = k8s_backend.pod_name(target)
            assert name == "pod-abc-123"
            args = mock_run.call_args[0][0]
            assert "-n" in args
            assert "espresense" in args
            assert "get" in args
            assert "pods" in args

    def test_pod_name_returns_empty_string_when_no_pod(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout="", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc):
            assert k8s_backend.pod_name(target) == ""

    def test_pod_name_returns_empty_string_when_whitespace_only(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout="   \n  ", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc):
            assert k8s_backend.pod_name(target) == ""


class TestExecStdinFlag:
    def test_exec_appends_i_flag_when_stdin_provided(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout=b"done", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc) as mock_run:
            k8s_backend.exec_(target, ["cat"], stdin="hello", check=True)
            args = mock_run.call_args[0][0]
            assert "-i" in args
            assert "--" in args
            # stdin should be encoded bytes
            assert mock_run.call_args[1].get("stdin") == b"hello"

    def test_exec_omits_i_flag_when_no_stdin(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout=b"done", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc) as mock_run:
            k8s_backend.exec_(target, ["ls"], check=True)
            args = mock_run.call_args[0][0]
            assert "-i" not in args
            assert mock_run.call_args[1].get("stdin") is None

    def test_exec_uses_text_false(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout=b"done", returncode=0)
        with patch.object(k8s_backend, "_run", return_value=mock_proc) as mock_run:
            k8s_backend.exec_(target, ["ls"], check=True)
            assert mock_run.call_args[1].get("text") is False


class TestRestart:
    def test_restart_calls_rollout_restart(self):
        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            k8s_backend.restart(target)
            args = mock_run.call_args[0][0]
            assert "rollout" in args
            assert "restart" in args
            assert "deployment/espresense-companion" in args
            assert mock_run.call_args[1].get("check") is True


class TestWriteConfigBackup:
    def test_write_config_creates_backup_when_enabled(self):
        target = k8s_backend.K8sTarget()
        ts_proc = MagicMock(stdout=b"1700000000\n", returncode=0)
        cp_proc = MagicMock(stdout=b"", returncode=0)
        dd_proc = MagicMock(stdout=b"", returncode=0)

        call_count = [0]

        def fake_exec(t, argv, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ts_proc
            if call_count[0] == 2:
                # backup cp call
                assert argv == ["cp", target.config_path, f"{target.config_path}.1700000000.bak"]
                assert kwargs.get("check") is False
                return cp_proc
            # dd call
            assert argv == ["dd", f"of={target.config_path}"]
            assert kwargs.get("stdin") == "new: config\n"
            assert kwargs.get("check") is True
            return dd_proc

        with patch.object(k8s_backend, "exec_", side_effect=fake_exec):
            k8s_backend.write_config(target, "new: config\n", backup=True)

    def test_write_config_skips_backup_when_disabled(self):
        target = k8s_backend.K8sTarget()
        ts_proc = MagicMock(stdout=b"1700000000\n", returncode=0)
        dd_proc = MagicMock(stdout=b"", returncode=0)

        call_count = [0]

        def fake_exec(t, argv, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ts_proc
            # Only the dd call should happen — no cp backup
            assert argv == ["dd", f"of={target.config_path}"]
            assert kwargs.get("stdin") == "data\n"
            return dd_proc

        with patch.object(k8s_backend, "exec_", side_effect=fake_exec):
            k8s_backend.write_config(target, "data\n", backup=False)

        assert call_count[0] == 2  # date + dd, no cp


class TestReadConfig:
    def test_read_config_decodes_stdout(self):
        target = k8s_backend.K8sTarget()
        mock_proc = MagicMock(stdout=b"key: value\n", returncode=0)
        with patch.object(k8s_backend, "exec_", return_value=mock_proc) as mock_exec:
            result = k8s_backend.read_config(target)
            assert result == "key: value\n"
            argv = mock_exec.call_args[0][1]
            assert argv == ["cat", "/config/espresense/config.yaml"]
            assert mock_exec.call_args[1].get("check") is True


# ── companion_client: post/put/delete convenience methods ────────────────────


class TestCompanionClientConvenienceMethods:
    def _mock_response(self, content=b'{"ok": true}', ctype="application/json"):
        resp = MagicMock()
        resp.content = content
        resp.headers = {"Content-Type": ctype}
        resp.json.return_value = {"ok": True}
        resp.status_code = 200
        return resp

    def test_post_returns_parsed_json(self):
        client = CompanionClient(base_url="http://host")
        with patch.object(
            client.session, "request", return_value=self._mock_response()
        ) as mock_req:
            result = client.post("/api/state/calibration/reset", json=True)
            assert result == {"ok": True}
            assert mock_req.call_args[0][0] == "POST"
            assert mock_req.call_args[1].get("json") is True

    def test_put_returns_parsed_json(self):
        client = CompanionClient(base_url="http://host")
        with patch.object(
            client.session, "request", return_value=self._mock_response()
        ) as mock_req:
            result = client.put("/api/node/n1", json={"room": "kitchen"})
            assert result == {"ok": True}
            assert mock_req.call_args[0][0] == "PUT"
            assert mock_req.call_args[1].get("json") == {"room": "kitchen"}

    def test_delete_returns_parsed_json(self):
        client = CompanionClient(base_url="http://host")
        with patch.object(
            client.session, "request", return_value=self._mock_response()
        ) as mock_req:
            result = client.delete("/api/node/n1")
            assert result == {"ok": True}
            assert mock_req.call_args[0][0] == "DELETE"

    def test_post_with_data_sends_data(self):
        client = CompanionClient(base_url="http://host")
        with patch.object(
            client.session, "request", return_value=self._mock_response()
        ) as mock_req:
            client.post("/api/raw", data="raw-body")
            assert mock_req.call_args[1].get("data") == "raw-body"

    def test_parse_returns_none_for_empty_content(self):
        resp = MagicMock()
        resp.content = b""
        assert CompanionClient._parse(resp) is None

    def test_parse_json_content_type_uses_json(self):
        resp = MagicMock()
        resp.content = b'{"x": 1}'
        resp.headers = {"Content-Type": "application/json; charset=utf-8"}
        resp.json.return_value = {"x": 1}
        assert CompanionClient._parse(resp) == {"x": 1}

    def test_parse_non_json_content_type_falls_back_to_json_then_text(self):
        """When content-type is not json but body IS valid json, _parse still tries json()."""
        resp = MagicMock()
        resp.content = b'{"x": 1}'
        resp.headers = {"Content-Type": "text/plain"}
        resp.json.return_value = {"x": 1}
        assert CompanionClient._parse(resp) == {"x": 1}

    def test_parse_non_json_invalid_json_falls_back_to_text(self):
        resp = MagicMock()
        resp.content = b"not json at all"
        resp.headers = {"Content-Type": "text/plain"}
        resp.json.side_effect = ValueError("nope")
        resp.text = "not json at all"
        assert CompanionClient._parse(resp) == "not json at all"

    def test_request_truncates_long_error_body(self):
        """Error message should only include first 500 chars of response body."""
        client = CompanionClient(base_url="http://host")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "x" * 1000
        with patch.object(client.session, "request", return_value=mock_resp):
            with pytest.raises(CompanionError) as exc_info:
                client.request("GET", "/api/state/config")
            # The error message should contain the truncated body, not the full 1000 chars
            msg = str(exc_info.value)
            assert "500" in msg
            # The body portion should be 500 chars (plus the prefix text)
            assert msg.count("x") == 500

    def test_request_empty_body_on_error(self):
        """When resp.text is empty, error message should not include body text."""
        client = CompanionClient(base_url="http://host")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = ""
        with patch.object(client.session, "request", return_value=mock_resp):
            with pytest.raises(CompanionError, match="404"):
                client.request("GET", "/api/missing")

    def test_request_uses_custom_timeout(self):
        """A per-call timeout should override the client default."""
        client = CompanionClient(base_url="http://host", timeout=30)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client.request("GET", "/api/state/config", timeout=5)
            assert mock_req.call_args[1].get("timeout") == 5

    def test_request_uses_default_timeout_when_none(self):
        client = CompanionClient(base_url="http://host", timeout=30)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client.request("GET", "/api/state/config")
            assert mock_req.call_args[1].get("timeout") == 30


# ── yaml_io: load_path and dump_path file I/O ────────────────────────────────


class TestYamlIoFileOperations:
    """load_path and dump_path are the file-backed variants of load/dumps."""

    def test_load_path_reads_file(self, tmp_path):
        from cli_anything.espresense.utils import yaml_io

        p = tmp_path / "config.yaml"
        p.write_text("key: value\nlist:\n  - a\n  - b\n")
        data = yaml_io.load_path(str(p))
        assert data["key"] == "value"
        assert list(data["list"]) == ["a", "b"]

    def test_dump_path_writes_file(self, tmp_path):
        from cli_anything.espresense.utils import yaml_io

        p = tmp_path / "out.yaml"
        yaml_io.dump_path({"name": "test", "count": 3}, str(p))
        written = p.read_text(encoding="utf-8")
        assert "name: test" in written
        assert "count: 3" in written

    def test_dump_path_round_trips_through_load_path(self, tmp_path):
        from cli_anything.espresense.utils import yaml_io

        p = tmp_path / "rt.yaml"
        original = {"room": "kitchen", "devices": ["phone", "watch"]}
        yaml_io.dump_path(original, str(p))
        loaded = yaml_io.load_path(str(p))
        assert loaded["room"] == "kitchen"
        assert list(loaded["devices"]) == ["phone", "watch"]
