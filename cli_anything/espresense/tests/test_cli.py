"""Tests for the espresense_cli module — helper functions and command behaviour.

Covers the pure rendering helpers (emit, _print_table, _abort) and exercises
real command paths via Click's CliRunner with mocked downstream dependencies.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli_anything.espresense import espresense_cli as cli_mod
from cli_anything.espresense.espresense_cli import cli


# ── emit: the output formatter has real branching on data type ───────────────


class TestEmit:
    """emit() dispatches on data type — each branch is real logic."""

    def _ctx(self, as_json=False):
        ctx = MagicMock()
        ctx.obj = {"as_json": as_json}
        return ctx

    def test_json_mode_serialises_dict(self, capsys):
        ctx = self._ctx(as_json=True)
        cli_mod.emit(ctx, {"b": 2, "a": 1})
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"a": 1, "b": 2}  # sort_keys=True

    def test_json_mode_serialises_list_of_dicts(self, capsys):
        ctx = self._ctx(as_json=True)
        cli_mod.emit(ctx, [{"x": 1}])
        parsed = json.loads(capsys.readouterr().out)
        assert parsed == [{"x": 1}]

    def test_none_emits_nothing(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, None)
        assert capsys.readouterr().out == ""

    def test_string_is_echoed_directly(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, "hello world")
        assert capsys.readouterr().out.strip() == "hello world"

    def test_list_of_strings_printed_one_per_line(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, ["a", "b", "c"])
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines == ["a", "b", "c"]

    def test_list_of_dicts_renders_table(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, [{"name": "alpha", "val": 1}, {"name": "beta", "val": 2}])
        out = capsys.readouterr().out
        # header row present
        assert "name" in out
        assert "val" in out
        # both data rows present
        assert "alpha" in out
        assert "beta" in out

    def test_dict_prints_key_value_lines(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, {"simple": "value", "count": 42})
        out = capsys.readouterr().out
        assert "simple: value" in out
        assert "count: 42" in out

    def test_dict_with_nested_value_uses_json(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, {"items": [1, 2, 3]})
        out = capsys.readouterr().out
        assert "items: [1, 2, 3]" in out

    def test_fallback_echoes_str_representation(self, capsys):
        ctx = self._ctx()
        cli_mod.emit(ctx, 12345)
        assert capsys.readouterr().out.strip() == "12345"


# ── _print_table: column discovery, truncation, formatting ───────────────────


class TestPrintTable:
    def test_excludes_underscore_and_raw_keys(self, capsys):
        cli_mod._print_table([{"name": "x", "_hidden": "secret", "raw": "data"}])
        out = capsys.readouterr().out
        assert "name" in out
        assert "_hidden" not in out
        assert "raw" not in out

    def test_truncates_long_values(self, capsys):
        long_val = "x" * 100
        cli_mod._print_table([{"col": long_val}])
        out = capsys.readouterr().out
        # truncated values end with "..."
        assert "..." in out
        assert long_val not in out

    def test_none_cell_shows_dash(self, capsys):
        cli_mod._print_table([{"col": None}])
        out = capsys.readouterr().out
        # the data row (not header) should contain a dash for None
        lines = out.strip().splitlines()
        assert any("-" in line for line in lines[2:])  # skip header + separator

    def test_float_formatted_two_decimals(self, capsys):
        cli_mod._print_table([{"val": 3.14159}])
        out = capsys.readouterr().out
        assert "3.14" in out

    def test_empty_rows_prints_nothing(self, capsys):
        cli_mod._print_table([])
        assert capsys.readouterr().out == ""

    def test_list_cell_truncated_as_json(self, capsys):
        cli_mod._print_table([{"col": list(range(20))}])
        out = capsys.readouterr().out
        assert "..." in out


# ── _abort: writes to stderr and exits ───────────────────────────────────────


class TestAbort:
    def test_writes_error_prefix_to_stderr(self):
        with pytest.raises(SystemExit) as exc_info:
            cli_mod._abort("something went wrong")
        assert exc_info.value.code == 1

    def test_message_contains_error_prefix(self, capsys):
        with pytest.raises(SystemExit):
            cli_mod._abort("boom")
        assert "error: boom" in capsys.readouterr().err


# ── make_client / make_k8s_target: build objects from ctx.obj ────────────────


class TestMakeClient:
    def test_builds_client_from_ctx_obj(self):
        ctx = MagicMock()
        ctx.obj = {"base_url": "http://h:1", "timeout": 5, "verify_ssl": False}
        client = cli_mod.make_client(ctx)
        assert client.base_url == "http://h:1"
        assert client.timeout == 5
        assert client.verify_ssl is False


class TestMakeK8sTarget:
    def test_builds_target_from_ctx_obj(self):
        ctx = MagicMock()
        ctx.obj = {
            "k8s_namespace": "ns",
            "k8s_deployment": "dep",
            "k8s_container": "ctr",
            "k8s_config_path": "/p/config.yaml",
        }
        target = cli_mod.make_k8s_target(ctx)
        assert target.namespace == "ns"
        assert target.deployment == "dep"
        assert target.container == "ctr"
        assert target.config_path == "/p/config.yaml"


# ── CLI commands via CliRunner (downstream mocked) ───────────────────────────


class TestCliConfigShow:
    def test_config_show_outputs_resolved_profile(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps({"base_url": "http://my-host:9999"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(cfg_path), "config", "show"])
        assert result.exit_code == 0
        assert "http://my-host:9999" in result.output


class TestCliConfigSave:
    def test_config_save_writes_file(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "out.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "--base-url", "http://saved:1234", "config", "save"],
        )
        assert result.exit_code == 0
        assert cfg_path.exists()
        loaded = json.loads(cfg_path.read_text())
        assert loaded["base_url"] == "http://saved:1234"


class TestCliCompanionApi:
    def test_companion_api_get_calls_client(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.text = ""
        mock_client.request.return_value = mock_resp
        with patch.object(cli_mod, "make_client", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "companion", "api", "GET", "/api/state/devices"],
            )
        assert result.exit_code == 0
        mock_client.request.assert_called_once_with("GET", "/api/state/devices")

    def test_companion_api_post_sends_json_body(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"created": True}
        mock_client.request.return_value = mock_resp
        with patch.object(cli_mod, "make_client", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(cfg_path),
                    "companion",
                    "api",
                    "POST",
                    "/api/state/config",
                    "--data",
                    '{"key":"val"}',
                ],
            )
        assert result.exit_code == 0
        mock_client.request.assert_called_once_with(
            "POST", "/api/state/config", json={"key": "val"}
        )

    def test_companion_api_non_json_response_echoes_text(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("no json")
        mock_resp.text = "plain text response"
        mock_client.request.return_value = mock_resp
        with patch.object(cli_mod, "make_client", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "companion", "api", "GET", "/raw"],
            )
        assert result.exit_code == 0
        assert "plain text response" in result.output


class TestCliCompanionInfo:
    def test_info_summarises_nodes_and_config(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.companion_api") as mock_api,
        ):
            mock_api.get_config.return_value = {
                "devices": ["d1", "d2"],
                "optimization": {"enabled": True},
            }
            mock_api.list_nodes.return_value = [
                {"id": "n1", "online": True},
                {"id": "n2", "online": False},
                {"id": "n3", "online": True},
            ]
            mock_api.get_calibration.return_value = {"r": 0.95, "rmse": 0.3}
            runner = CliRunner()
            result = runner.invoke(cli, ["--config", str(cfg_path), "companion", "info"])
        assert result.exit_code == 0
        assert "node_count" in result.output
        assert "2" in result.output  # online count

    def test_info_aborts_on_companion_error(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        from cli_anything.espresense.utils.companion_client import CompanionError

        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.companion_api") as mock_api,
        ):
            mock_api.get_config.side_effect = CompanionError("connection refused")
            runner = CliRunner()
            result = runner.invoke(cli, ["--config", str(cfg_path), "companion", "info"])
        assert result.exit_code == 1
        assert "connection refused" in result.output


class TestCliRoomsRotate:
    def test_rotate_rejects_map_without_equals(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "rooms", "rotate", "--map", "no_equals_here"],
        )
        assert result.exit_code == 1
        assert "old=new" in result.output

    def test_rotate_rejects_empty_side(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "rooms", "rotate", "--map", "=newname"],
        )
        assert result.exit_code == 1
        assert "empty side" in result.output


class TestCliNodeSet:
    def test_node_set_rejects_field_without_equals(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "node", "set", "10.0.0.1", "no_equals"],
        )
        assert result.exit_code == 1
        assert "key=value" in result.output

    def test_node_set_rejects_no_fields(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "node", "set", "10.0.0.1"],
        )
        assert result.exit_code == 1
        assert "no fields" in result.output

    def test_node_set_sends_payload(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_node = MagicMock()
        mock_node.put_settings.return_value = {"ok": True}
        with patch.object(cli_mod, "_node_client", return_value=mock_node):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(cfg_path),
                    "node",
                    "set",
                    "10.0.0.1",
                    "absorption=2.8",
                    "tx_ref_rssi=-59",
                ],
            )
        assert result.exit_code == 0
        mock_node.put_settings.assert_called_once_with(
            "extras", {"absorption": "2.8", "tx_ref_rssi": "-59"}
        )


class TestCliMqttArgs:
    def test_mqtt_set_node_aborts_without_host(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--config", str(cfg_path), "mqtt", "set-node", "node1", "key", "val"],
        )
        assert result.exit_code == 1
        assert "no MQTT broker" in result.output

    def test_mqtt_pub_publishes_when_configured(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        with patch("cli_anything.espresense.espresense_cli.mqtt_core") as mock_mqtt:
            mock_mqtt.publish_raw.return_value = {"topic": "test/t", "published": True}
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "mqtt", "pub", "test/t", "hello"],
            )
        assert result.exit_code == 0
        mock_mqtt.publish_raw.assert_called_once()
        call_kw = mock_mqtt.publish_raw.call_args[1]
        assert call_kw["topic"] == "test/t"
        assert call_kw["payload"] == "hello"


class TestCliHistoryGet:
    def test_history_get_applies_limit(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.history_core") as mock_hist,
        ):
            mock_hist.get_history.return_value = [{"ts": f"t{i}"} for i in range(10)]
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "--json", "history", "get", "dev1", "--limit", "3"],
            )
        assert result.exit_code == 0
        # limit takes the last 3
        rows = json.loads(result.output)
        assert len(rows) == 3
        assert rows[-1]["ts"] == "t9"


class TestCliCalibrationAuto:
    def test_auto_optimize_status(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.calibration_core") as mock_cal,
        ):
            mock_cal.auto_optimize_get.return_value = {"enabled": True}
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "calibration", "auto-optimize", "status"],
            )
        assert result.exit_code == 0
        mock_cal.auto_optimize_get.assert_called_once()

    def test_auto_optimize_on_calls_set(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.calibration_core") as mock_cal,
        ):
            mock_cal.auto_optimize_set.return_value = {"enabled": True}
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "calibration", "auto-optimize", "on"],
            )
        assert result.exit_code == 0
        mock_cal.auto_optimize_set.assert_called_once_with(mock_client, True)


class TestCliCompanionConfigGet:
    def test_config_get_json_format(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        mock_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=mock_client),
            patch("cli_anything.espresense.espresense_cli.companion_api") as mock_api,
        ):
            mock_api.get_config.return_value = {"rooms": ["kitchen"]}
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(cfg_path),
                    "--json",
                    "companion",
                    "config-get",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed == {"rooms": ["kitchen"]}


class TestCliNodesList:
    def test_nodes_list_config_only_when_no_merge(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        with (
            patch("cli_anything.espresense.espresense_cli.config_core") as mock_cfg,
            patch("cli_anything.espresense.espresense_cli.nodes_core") as mock_nodes,
        ):
            mock_cfg.fetch_yaml.return_value = ("raw", {"nodes": []})
            mock_nodes.list_config_nodes.return_value = [{"name": "n1"}]
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "nodes", "list", "--no-merge-live"],
            )
        assert result.exit_code == 0
        mock_nodes.list_config_nodes.assert_called_once()

    def test_nodes_list_falls_back_on_companion_error(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg_path)
        from cli_anything.espresense.utils.companion_client import CompanionError

        mock_client = MagicMock()
        with (
            patch("cli_anything.espresense.espresense_cli.config_core") as mock_cfg,
            patch("cli_anything.espresense.espresense_cli.nodes_core") as mock_nodes,
            patch.object(cli_mod, "make_client", return_value=mock_client),
        ):
            mock_cfg.fetch_yaml.return_value = ("raw", {"nodes": []})
            mock_nodes.list_live_nodes.side_effect = CompanionError("unreachable")
            mock_nodes.list_config_nodes.return_value = [{"name": "fallback"}]
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["--config", str(cfg_path), "nodes", "list"],
            )
        assert result.exit_code == 0
        assert "fallback" in result.output
