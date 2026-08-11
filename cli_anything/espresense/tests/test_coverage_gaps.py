"""Tests for previously uncovered core modules.

Covers real logic in project.py, calibration.py, devices.py, history.py,
config_yaml.py, companion_client.py, and node_direct.py — error paths,
edge cases, and branches that the existing suite never exercised.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cli_anything.espresense.core import calibration, config_yaml, devices, history
from cli_anything.espresense.core import project
from cli_anything.espresense.core.companion_api import (
    get_device_history,
)
from cli_anything.espresense.core.node_direct import NodeClient, NodeError
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError


# ── project.py ───────────────────────────────────────────────────────────────


class TestProjectLoadConfig:
    """load_config: defaults, file override, env override, corrupt-file safety."""

    def test_returns_defaults_when_no_file(self, tmp_path):
        cfg = project.load_config(tmp_path / "nonexistent.json")
        if cfg["base_url"] != "http://localhost:8267":
            pytest.fail(f"unexpected base_url: {cfg['base_url']}")
        if cfg["verify_ssl"] is not True:
            pytest.fail("verify_ssl should default to True")
        if cfg["timeout"] != 30:
            pytest.fail(f"unexpected timeout: {cfg['timeout']}")
        if cfg["mqtt_host"] is not None:
            pytest.fail("mqtt_host should default to None")

    def test_file_overrides_defaults(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"base_url": "http://node:9999", "timeout": 60}))
        cfg = project.load_config(p)
        if cfg["base_url"] != "http://node:9999":
            pytest.fail("file override for base_url not applied")
        if cfg["timeout"] != 60:
            pytest.fail("file override for timeout not applied")
        # keys not in the file keep their defaults
        if cfg["verify_ssl"] is not True:
            pytest.fail("verify_ssl should still be the default")

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("{not valid json")
        cfg = project.load_config(p)
        # should not raise; defaults survive
        if cfg["base_url"] != "http://localhost:8267":
            pytest.fail("corrupt file should fall back to defaults")

    def test_env_bool_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_ESPRESENSE_VERIFY_SSL", "false")
        cfg = project.load_config(tmp_path / "nonexistent.json")
        if cfg["verify_ssl"] is not False:
            pytest.fail(f"env override should set verify_ssl=False, got {cfg['verify_ssl']}")

    def test_env_int_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_ESPRESENSE_TIMEOUT", "120")
        cfg = project.load_config(tmp_path / "nonexistent.json")
        if cfg["timeout"] != 120:
            pytest.fail(f"env override should set timeout=120, got {cfg['timeout']}")

    def test_env_int_override_non_numeric_falls_back_to_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_ESPRESENSE_TIMEOUT", "abc")
        cfg = project.load_config(tmp_path / "nonexistent.json")
        if cfg["timeout"] != "abc":
            pytest.fail(
                f"non-numeric env int override should fall back to string, got {cfg['timeout']}"
            )

    def test_env_string_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_ESPRESENSE_BASE_URL", "http://env-host:8080")
        cfg = project.load_config(tmp_path / "nonexistent.json")
        if cfg["base_url"] != "http://env-host:8080":
            pytest.fail(f"env string override not applied, got {cfg['base_url']}")


class TestProjectSaveConfig:
    def test_save_and_reload_round_trip(self, tmp_path):
        p = tmp_path / "sub" / "cfg.json"
        cfg = {"base_url": "http://saved:1234", "timeout": 45}
        written = project.save_config(cfg, path=p)
        if written != p:
            pytest.fail("save_config should return the path it wrote")
        if not p.exists():
            pytest.fail("config file was not created")
        loaded = project.load_config(p)
        if loaded["base_url"] != "http://saved:1234":
            pytest.fail("round-trip did not preserve base_url")
        if loaded["timeout"] != 45:
            pytest.fail("round-trip did not preserve timeout")

    def test_save_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c" / "cfg.json"
        project.save_config({"timeout": 10}, path=p)
        if not p.exists():
            pytest.fail("save_config should create parent directories")


class TestProjectMergeCliOverrides:
    def test_none_values_are_ignored(self):
        cfg = {"base_url": "http://orig", "timeout": 30}
        out = project.merge_cli_overrides(cfg, base_url=None, timeout=99)
        if out["base_url"] != "http://orig":
            pytest.fail("None override should not replace existing value")
        if out["timeout"] != 99:
            pytest.fail("non-None override should replace existing value")

    def test_does_not_mutate_original(self):
        cfg = {"base_url": "http://orig"}
        project.merge_cli_overrides(cfg, base_url="http://new")
        if cfg["base_url"] != "http://orig":
            pytest.fail("merge_cli_overrides must not mutate the input dict")


# ── calibration.py ───────────────────────────────────────────────────────────


class TestCalibrationSummary:
    """summary() has real branching for matrix dict vs non-dict vs missing."""

    def _client(self, cal_data):
        client = MagicMock()
        client.get = MagicMock(return_value=cal_data)
        return client

    def test_summary_no_matrix(self):
        client = self._client({"r": 0.9, "rmse": 1.2})
        result = calibration.summary(client)
        if result["pair_count"] != 0:
            pytest.fail("missing matrix should yield pair_count=0")
        if result["r"] != 0.9 or result["rmse"] != 1.2:
            pytest.fail("r and rmse should pass through from cal data")

    def test_summary_with_matrix_dict(self):
        matrix = {"node_a": {"dev1": 1, "dev2": 2}, "node_b": {"dev3": 3}}
        client = self._client({"r": 0.8, "rmse": 0.5, "matrix": matrix})
        result = calibration.summary(client)
        if result["pair_count"] != 3:
            pytest.fail(f"expected pair_count=3, got {result['pair_count']}")

    def test_summary_matrix_not_dict(self):
        client = self._client({"r": 0.7, "rmse": 2.0, "matrix": "not-a-dict"})
        result = calibration.summary(client)
        if result["pair_count"] != 0:
            pytest.fail("non-dict matrix should yield pair_count=0")

    def test_summary_matrix_values_not_dicts(self):
        matrix = {"node_a": "not-a-dict", "node_b": 42}
        client = self._client({"r": 0.6, "rmse": 3.0, "matrix": matrix})
        result = calibration.summary(client)
        if result["pair_count"] != 0:
            pytest.fail("non-dict matrix values should not be counted")

    def test_summary_cal_not_dict_raises_attribute_error(self):
        """summary() assumes cal is a dict; a non-dict response raises AttributeError.

        This documents the actual behaviour: the code calls cal.get("r")
        unconditionally, so a non-dict response is not gracefully handled.
        """
        client = self._client("not-a-dict")
        with pytest.raises(AttributeError):
            calibration.summary(client)


# ── devices.py ──────────────────────────────────────────────────────────────


class TestDevicesListDevices:
    """list_devices transforms raw API output; room/floor can be dict or scalar."""

    def _client(self, raw_devices):
        client = MagicMock()
        client.get = MagicMock(return_value=raw_devices)
        return client

    def test_room_and_floor_as_dicts(self):
        raw = [
            {
                "id": "dev1",
                "name": "Phone",
                "room": {"name": "Kitchen"},
                "floor": {"name": "Ground"},
                "lastHit": "2024-01-01",
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "confidence": 99,
                "configuredRefRssi": -65,
            }
        ]
        client = self._client(raw)
        result = devices.list_devices(client)
        if len(result) != 1:
            pytest.fail(f"expected 1 device, got {len(result)}")
        d = result[0]
        if d["room"] != "Kitchen":
            pytest.fail(f"room dict should extract name, got {d['room']}")
        if d["floor"] != "Ground":
            pytest.fail(f"floor dict should extract name, got {d['floor']}")
        if d["last_seen"] != "2024-01-01":
            pytest.fail("lastHit should map to last_seen")
        if d["ref_rssi"] != -65:
            pytest.fail("configuredRefRssi should map to ref_rssi")

    def test_room_and_floor_as_scalars(self):
        raw = [
            {
                "id": "dev2",
                "name": "Laptop",
                "room": "Hall",
                "floor": "First",
                "lastSeen": "2024-02-01",
                "refRssi": -70,
            }
        ]
        client = self._client(raw)
        result = devices.list_devices(client)
        d = result[0]
        if d["room"] != "Hall":
            pytest.fail(f"scalar room should pass through, got {d['room']}")
        if d["floor"] != "First":
            pytest.fail(f"scalar floor should pass through, got {d['floor']}")
        if d["last_seen"] != "2024-02-01":
            pytest.fail("lastSeen should map to last_seen when lastHit absent")
        if d["ref_rssi"] != -70:
            pytest.fail("refRssi should map to ref_rssi when configuredRefRssi absent")

    def test_empty_list(self):
        client = self._client([])
        result = devices.list_devices(client)
        if result != []:
            pytest.fail("empty input should yield empty output")

    def test_show_all_flag_passed_through(self):
        client = MagicMock()
        client.get = MagicMock(return_value=[])
        devices.list_devices(client, show_all=True)
        call_kwargs = client.get.call_args[1]
        if call_kwargs.get("params", {}).get("showAll") != "true":
            pytest.fail("show_all=True should pass showAll=true to API")


class TestDevicesUpdateDevice:
    """update_device builds a settings dict; returns None when nothing to set."""

    def _client(self):
        client = MagicMock()
        client.put = MagicMock(return_value={"ok": True})
        return client

    def test_no_settings_returns_none(self):
        client = self._client()
        result = devices.update_device(client, "dev1")
        if result is not None:
            pytest.fail("update_device with no settings should return None")
        client.put.assert_not_called()

    def test_name_only(self):
        client = self._client()
        devices.update_device(client, "dev1", name="NewName")
        sent = client.put.call_args[1]["json"]
        if sent != {"name": "NewName"}:
            pytest.fail(f"expected only name in settings, got {sent}")

    def test_ref_rssi_uses_capitalized_key(self):
        client = self._client()
        devices.update_device(client, "dev1", ref_rssi=-60)
        sent = client.put.call_args[1]["json"]
        if "RefRssi" not in sent or sent["RefRssi"] != -60:
            pytest.fail(f"ref_rssi should map to RefRssi key, got {sent}")

    def test_anchored_coords_grouped(self):
        client = self._client()
        devices.update_device(client, "dev1", anchored_x=1.0, anchored_y=2.0, anchored_z=3.0)
        sent = client.put.call_args[1]["json"]
        if sent.get("anchored") != {"x": 1.0, "y": 2.0, "z": 3.0}:
            pytest.fail(f"anchored coords should be grouped, got {sent}")

    def test_partial_anchored_uses_setdefault(self):
        client = self._client()
        devices.update_device(client, "dev1", anchored_x=5.0, anchored_y=6.0)
        sent = client.put.call_args[1]["json"]
        if sent.get("anchored") != {"x": 5.0, "y": 6.0}:
            pytest.fail(f"partial anchored should still group, got {sent}")


class TestDevicesDeleteDevice:
    def test_delete_calls_api(self):
        client = MagicMock()
        client.delete = MagicMock(return_value=None)
        devices.delete_device(client, "dev1")
        client.delete.assert_called_once_with("/api/device/dev1")


# ── history.py ──────────────────────────────────────────────────────────────


class TestHistoryGetHistory:
    """get_history handles dict responses, list responses, and other types."""

    def _client(self, return_value):
        client = MagicMock()
        client.get = MagicMock(return_value=return_value)
        return client

    def test_dict_with_history_key(self):
        client = self._client({"history": [{"ts": 1}, {"ts": 2}]})
        result = history.get_history(client, "dev1")
        if result != [{"ts": 1}, {"ts": 2}]:
            pytest.fail("should extract history list from dict")

    def test_dict_with_empty_history(self):
        client = self._client({"history": []})
        result = history.get_history(client, "dev1")
        if result != []:
            pytest.fail("empty history should yield empty list")

    def test_dict_without_history_key(self):
        client = self._client({"other": "data"})
        result = history.get_history(client, "dev1")
        if result != []:
            pytest.fail("dict without history key should yield empty list")

    def test_list_response(self):
        data = [{"ts": 1}]
        client = self._client(data)
        result = history.get_history(client, "dev1")
        if result != data:
            pytest.fail("list response should pass through")

    def test_other_type_response(self):
        client = self._client("not-a-list-or-dict")
        result = history.get_history(client, "dev1")
        if result != []:
            pytest.fail("non-list/non-dict response should yield empty list")

    def test_start_end_params_passed_to_range_endpoint(self):
        client = self._client({"history": []})
        history.get_history(client, "dev1", start="2024-01-01", end="2024-02-01")
        call_args = client.get.call_args
        path = call_args[0][0]
        if "/range" not in path:
            pytest.fail(f"start/end should use /range endpoint, got {path}")
        params = call_args[1].get("params", {})
        if params.get("start") != "2024-01-01" or params.get("end") != "2024-02-01":
            pytest.fail(f"start/end params not passed correctly: {params}")

    def test_only_start_uses_range_endpoint(self):
        client = self._client({"history": []})
        history.get_history(client, "dev1", start="2024-01-01")
        path = client.get.call_args[0][0]
        if "/range" not in path:
            pytest.fail("start-only should still use /range endpoint")


# ── companion_api.get_device_history ─────────────────────────────────────────


class TestCompanionApiGetDeviceHistory:
    def test_no_start_end_uses_simple_endpoint(self):
        client = MagicMock()
        client.get = MagicMock(return_value={})
        get_device_history(client, "dev1")
        path = client.get.call_args[0][0]
        if "/range" in path:
            pytest.fail("no start/end should use simple endpoint, not /range")

    def test_only_end_uses_range_endpoint(self):
        client = MagicMock()
        client.get = MagicMock(return_value={})
        get_device_history(client, "dev1", end="2024-02-01")
        path = client.get.call_args[0][0]
        if "/range" not in path:
            pytest.fail("end-only should use /range endpoint")


# ── companion_client.py ─────────────────────────────────────────────────────


class TestCompanionClientUrl:
    def test_url_prepends_slash(self):
        c = CompanionClient(base_url="http://host:8080")
        if c._url("api/state") != "http://host:8080/api/state":
            pytest.fail("missing leading slash should be added")

    def test_url_preserves_slash(self):
        c = CompanionClient(base_url="http://host:8080")
        if c._url("/api/state") != "http://host:8080/api/state":
            pytest.fail("existing leading slash should be preserved")

    def test_url_passes_through_absolute(self):
        c = CompanionClient(base_url="http://host:8080")
        if c._url("http://other/full") != "http://other/full":
            pytest.fail("absolute URL should pass through unchanged")

    def test_base_url_trailing_slash_stripped(self):
        c = CompanionClient(base_url="http://host:8080/")
        if c.base_url != "http://host:8080":
            pytest.fail("trailing slash should be stripped from base_url")


class TestCompanionClientRequest:
    def test_request_raises_on_http_error(self):
        client = CompanionClient(base_url="http://localhost:1")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch.object(client.session, "request", return_value=mock_resp):
            with pytest.raises(CompanionError, match="500"):
                client.request("GET", "/api/state/config")

    def test_request_raises_on_connection_error(self):
        client = CompanionClient(base_url="http://localhost:1")
        import requests

        with patch.object(
            client.session, "request", side_effect=requests.ConnectionError("refused")
        ):
            with pytest.raises(CompanionError, match="failed"):
                client.request("GET", "/api/state/config")

    def test_request_passes_verify_ssl(self):
        client = CompanionClient(base_url="http://host", verify_ssl=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client.request("GET", "/api/state/config")
            call_kwargs = mock_req.call_args[1]
            if call_kwargs.get("verify") is not False:
                pytest.fail("verify_ssl=False should be passed to session.request")


class TestCompanionClientParse:
    def test_empty_content_returns_none(self):
        resp = MagicMock()
        resp.content = b""
        if CompanionClient._parse(resp) is not None:
            pytest.fail("empty content should return None")

    def test_json_content_type_parsed(self):
        resp = MagicMock()
        resp.content = b'{"key": "val"}'
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"key": "val"}
        result = CompanionClient._parse(resp)
        if result != {"key": "val"}:
            pytest.fail("json content-type should be parsed as dict")

    def test_non_json_falls_back_to_text(self):
        resp = MagicMock()
        resp.content = b"plain text"
        resp.headers = {"Content-Type": "text/plain"}
        resp.json.side_effect = ValueError("not json")
        resp.text = "plain text"
        result = CompanionClient._parse(resp)
        if result != "plain text":
            pytest.fail("non-json content should fall back to text")


# ── node_direct.py ───────────────────────────────────────────────────────────


class TestNodeClientUrl:
    def test_plain_host_gets_scheme_and_port(self):
        c = NodeClient("192.168.1.10")
        if c.base_url != "http://192.168.1.10:80":
            pytest.fail(f"plain host should get http://host:80, got {c.base_url}")

    def test_http_prefix_preserved(self):
        c = NodeClient("http://node.local")
        if c.base_url != "http://node.local":
            pytest.fail(f"http:// prefix should be preserved, got {c.base_url}")

    def test_https_prefix_preserved(self):
        c = NodeClient("https://secure.node:443")
        if c.base_url != "https://secure.node:443":
            pytest.fail(f"https:// prefix should be preserved, got {c.base_url}")

    def test_trailing_slash_stripped(self):
        c = NodeClient("http://node.local/")
        if c.base_url != "http://node.local":
            pytest.fail("trailing slash should be stripped")

    def test_custom_scheme_and_port(self):
        c = NodeClient("node.local", port=8080, scheme="https")
        if c.base_url != "https://node.local:8080":
            pytest.fail(f"custom scheme/port not applied, got {c.base_url}")

    def test_url_method_adds_slash(self):
        c = NodeClient("host")
        if c._url("json") != "http://host:80/json":
            pytest.fail("_url should add leading slash")
        if c._url("/json") != "http://host:80/json":
            pytest.fail("_url should preserve leading slash")


class TestNodeClientRequestError:
    def test_request_exception_wrapped_as_node_error(self):
        import requests

        c = NodeClient("host")
        with patch.object(c.session, "request", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(NodeError, match="failed"):
                c._request("GET", "/json")


class TestNodeClientInfo:
    def test_info_raises_on_http_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "boom"
        with patch.object(c.session, "request", return_value=mock_resp):
            with pytest.raises(NodeError, match="500"):
                c.info()

    def test_info_returns_json(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"room": "kitchen"})
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.info()
            if result != {"room": "kitchen"}:
                pytest.fail("info should return parsed JSON")

    def test_info_falls_back_on_bad_json(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(side_effect=ValueError("bad json"))
        mock_resp.text = "not json"
        mock_resp.headers = {"Content-Type": "text/html"}
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.info()
            if result.get("raw") != "not json":
                pytest.fail("bad JSON should fall back to raw text")

    def test_info_show_all_passes_param(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={})
        with patch.object(c.session, "request", return_value=mock_resp) as mock_req:
            c.info(show_all=True)
            params = mock_req.call_args[1].get("params")
            if params != {"showAll": "1"}:
                pytest.fail(f"show_all should pass showAll=1, got {params}")


class TestNodeClientSettings:
    def test_get_settings_rejects_invalid_section(self):
        c = NodeClient("host")
        with pytest.raises(ValueError, match="section must be one of"):
            c.get_settings("bad")

    def test_put_settings_rejects_invalid_section(self):
        c = NodeClient("host")
        with pytest.raises(ValueError, match="section must be one of"):
            c.put_settings("bad", {"key": "val"})

    def test_put_settings_converts_none_to_empty_string(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        with patch.object(c.session, "request", return_value=mock_resp) as mock_req:
            c.put_settings("main", {"room": None, "name": "test"})
            data = mock_req.call_args[1].get("data")
            if data.get("room") != "":
                pytest.fail("None value should become empty string in form data")

    def test_get_settings_raises_on_http_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "not found"
        with patch.object(c.session, "request", return_value=mock_resp):
            with pytest.raises(NodeError, match="404"):
                c.get_settings("main")

    def test_get_settings_returns_json(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"room": "hall"})
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.get_settings("extras")
            if result != {"room": "hall"}:
                pytest.fail("get_settings should return parsed JSON")

    def test_get_settings_falls_back_on_bad_json(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(side_effect=ValueError("bad"))
        mock_resp.text = "raw text"
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.get_settings("hardware")
            if result.get("raw") != "raw text":
                pytest.fail("bad JSON should fall back to raw text")


class TestNodeClientRestart:
    def test_restart_returns_true_on_success(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(c.session, "request", return_value=mock_resp):
            if c.restart() is not True:
                pytest.fail("restart should return True on 2xx")

    def test_restart_returns_false_on_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch.object(c.session, "request", return_value=mock_resp):
            if c.reboot() is not False:
                pytest.fail("reboot should return False on 5xx")


class TestNodeClientRename:
    def test_rename_rejects_empty_name(self):
        c = NodeClient("host")
        with pytest.raises(ValueError, match="non-empty"):
            c.rename("")

    def test_rename_rejects_slash_in_name(self):
        c = NodeClient("host")
        with pytest.raises(ValueError, match="non-empty"):
            c.rename("bad/name")

    def test_rename_calls_put_settings_and_restart(self):
        c = NodeClient("host")
        put_resp = MagicMock()
        put_resp.status_code = 200
        put_resp.text = "ok"
        restart_resp = MagicMock()
        restart_resp.status_code = 200
        with patch.object(c.session, "request", side_effect=[put_resp, restart_resp]):
            result = c.rename("kitchen")
            if result["new_name"] != "kitchen":
                pytest.fail("rename should return the new name")
            if result["post_status"] != 200:
                pytest.fail("rename should report the POST status")


class TestNodeClientDeviceConfigs:
    def test_list_device_configs_extracts_from_info(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"configs": [{"id": "d1"}]})
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.list_device_configs()
            if result != [{"id": "d1"}]:
                pytest.fail("list_device_configs should extract configs from info")

    def test_list_device_configs_empty_when_no_configs_key(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={})
        with patch.object(c.session, "request", return_value=mock_resp):
            if c.list_device_configs() != []:
                pytest.fail("missing configs key should yield empty list")

    def test_upsert_device_config_sends_payload(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"ok": True})
        with patch.object(c.session, "request", return_value=mock_resp) as mock_req:
            c.upsert_device_config("dev1", alias="phone", name="Phone", rssi_at_1m=-65)
            sent_json = mock_req.call_args[1].get("json")
            if sent_json.get("id") != "dev1":
                pytest.fail("upsert should include device id")
            if sent_json.get("alias") != "phone":
                pytest.fail("upsert should include alias")
            if sent_json.get("rssi@1m") != -65:
                pytest.fail("upsert should include rssi@1m")

    def test_upsert_device_config_raises_on_http_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        with patch.object(c.session, "request", return_value=mock_resp):
            with pytest.raises(NodeError, match="400"):
                c.upsert_device_config("dev1")

    def test_delete_device_config_returns_bool(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(c.session, "request", return_value=mock_resp):
            if c.delete_device_config("dev1") is not True:
                pytest.fail("delete should return True on 2xx")

    def test_delete_device_config_returns_false_on_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(c.session, "request", return_value=mock_resp):
            if c.delete_device_config("dev1") is not False:
                pytest.fail("delete should return False on 4xx")


class TestNodeClientScanWifi:
    def test_scan_returns_list(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=[{"ssid": "net1"}])
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.scan_wifi()
            if result != [{"ssid": "net1"}]:
                pytest.fail("scan_wifi should return list of networks")

    def test_scan_returns_single_dict_wrapped(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"ssid": "net1"})
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.scan_wifi()
            if len(result) != 1 or result[0]["ssid"] != "net1":
                pytest.fail("non-list JSON should be wrapped in a list")

    def test_scan_falls_back_on_bad_json(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(side_effect=ValueError("bad"))
        mock_resp.text = "raw"
        with patch.object(c.session, "request", return_value=mock_resp):
            result = c.scan_wifi()
            if result != [{"raw": "raw"}]:
                pytest.fail("bad JSON should fall back to raw text")

    def test_scan_raises_on_http_error(self):
        c = NodeClient("host")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "err"
        with patch.object(c.session, "request", return_value=mock_resp):
            with pytest.raises(NodeError, match="500"):
                c.scan_wifi()


# ── config_yaml.py ───────────────────────────────────────────────────────────


class TestConfigYamlFloorHelpers:
    """first_floor and find_floor have real KeyError paths."""

    def test_first_floor_returns_first(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("floors:\n  - id: g\n  - id: f\n")
        result = config_yaml.first_floor(parsed)
        if result.get("id") != "g":
            pytest.fail("first_floor should return the first floor entry")

    def test_first_floor_raises_on_empty(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("floors: []\n")
        with pytest.raises(KeyError, match="floors"):
            config_yaml.first_floor(parsed)

    def test_first_floor_raises_on_missing(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("other: value\n")
        with pytest.raises(KeyError, match="floors"):
            config_yaml.first_floor(parsed)

    def test_find_floor_returns_match(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("floors:\n  - id: g\n    name: Ground\n  - id: f\n    name: First\n")
        result = config_yaml.find_floor(parsed, "f")
        if result.get("name") != "First":
            pytest.fail("find_floor should return the matching floor")

    def test_find_floor_raises_on_no_match(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("floors:\n  - id: g\n")
        with pytest.raises(KeyError, match="no floor"):
            config_yaml.find_floor(parsed, "nonexistent")


class TestConfigYamlPushYaml:
    def test_push_yaml_summary_no_restart(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("key: value\n")
        target = MagicMock()
        with patch("cli_anything.espresense.core.config_source.k8s_backend") as mock_k8s:
            mock_k8s.write_config = MagicMock()
            mock_k8s.restart = MagicMock()
            summary = config_yaml.push_yaml(target, parsed, restart=False, backup=True)
            if summary["restarted"] is not False:
                pytest.fail("restarted should be False when restart=False")
            if summary["backed_up"] is not True:
                pytest.fail("backed_up should be True when backup=True")
            if summary["bytes_written"] <= 0:
                pytest.fail("bytes_written should be positive")
            mock_k8s.restart.assert_not_called()

    def test_push_yaml_summary_with_restart(self):
        from cli_anything.espresense.utils import yaml_io

        parsed = yaml_io.load("key: value\n")
        target = MagicMock()
        with patch("cli_anything.espresense.core.config_source.k8s_backend") as mock_k8s:
            mock_k8s.write_config = MagicMock()
            mock_k8s.restart = MagicMock()
            summary = config_yaml.push_yaml(target, parsed, restart=True, backup=False)
            if summary["restarted"] is not True:
                pytest.fail("restarted should be True when restart=True")
            if summary["backed_up"] is not False:
                pytest.fail("backed_up should be False when backup=False")
            mock_k8s.restart.assert_called_once_with(target)

    def test_fetch_yaml_returns_raw_and_parsed(self):
        target = MagicMock()
        with patch("cli_anything.espresense.core.config_source.k8s_backend") as mock_k8s:
            mock_k8s.read_config = MagicMock(return_value="key: value\n")
            raw, parsed = config_yaml.fetch_yaml(target)
            if raw != "key: value\n":
                pytest.fail("fetch_yaml should return raw text")
            if parsed.get("key") != "value":
                pytest.fail("fetch_yaml should return parsed YAML")
