"""Refine pass: hardening the interactive, streaming and direct-node surfaces.

Covers the branches earlier passes left behind:
- the REPL main loop and the root no-subcommand fallback,
- the prompt-toolkit skin's interactive paths (prompt session, toolbar, banner),
- websocket stream edge cases (a broken callback, Ctrl-C, close failures),
- mqtt.watch's run-forever mode,
- companion REST convenience posts,
- the direct-node (firmware web server) command group end to end,
- and the remaining CLI error/edge branches (coordinate parse aborts,
  settings/floors/rooms error exits, mqtt set-* families, calibration).
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli_anything.espresense import espresense_cli as cli_mod
from cli_anything.espresense.core import mqtt
from cli_anything.espresense.core import stream as stream_mod
from cli_anything.espresense.core.mqtt import MqttError
from cli_anything.espresense.core.stream import stream
from cli_anything.espresense.espresense_cli import cli
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError
from cli_anything.espresense.utils.repl_skin import _CYAN, ReplSkin

CONFIG_YAML = """\
mqtt:
  host: broker.local
  port: 1883
locators:
  nelder_mead:
    enabled: false
devices:
  - id: "irk:aaa"
    name: Phone
    "rssi@1m": -65
floors:
  - id: gf
    name: Ground Floor
    rooms:
      - name: Office
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
nodes:
  - name: office-node
    room: Office
    point: [1.0, 2.0, 2.5]
"""


def _profile(tmp_path, monkeypatch, body="{}"):
    cfg = tmp_path / "profile.json"
    cfg.write_text(body if isinstance(body, str) else json.dumps(body))
    monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
    return str(cfg)


# ────────────────────────────────────────────── REPL main loop


class _FakeSkin:
    """Scripted stand-in for ReplSkin: get_input replays a fixed script."""

    script: list = []
    instances: list = []

    def __init__(self, *args, **kwargs):
        self._inputs = iter(list(type(self).script))
        self.banners = 0
        self.goodbyes = 0
        self.help_arg = None
        self.errors: list[str] = []
        type(self).instances.append(self)

    def print_banner(self):
        self.banners += 1

    def create_prompt_session(self):
        return object()

    def get_input(self, session):
        return next(self._inputs)

    def print_goodbye(self):
        self.goodbyes += 1

    def help(self, commands):
        self.help_arg = commands

    def error(self, message):
        self.errors.append(message)


def _fake_skin_class(script):
    cls = type("ScriptedSkin", (_FakeSkin,), {})
    cls.script = script
    cls.instances = []
    return cls


class TestReplLoop:
    def _run(self, script, extra_argv=(), cfg=None):
        skin_cls = _fake_skin_class(script)
        with (
            patch(
                "cli_anything.espresense.utils.repl_skin.ReplSkin",
                skin_cls,
            ) as mock_skin,
            patch("cli_anything.espresense.espresense_cli.mqtt_core") as mock_mqtt,
        ):
            mock_mqtt.publish_raw.return_value = {"published": True}
            result = CliRunner().invoke(cli, [*list(extra_argv), "repl"], obj={})
        return result, mock_skin

    def test_exit_breaks_the_loop_and_says_goodbye(self):
        skin_cls = _fake_skin_class(["", "help", "exit"])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            result = CliRunner().invoke(cli, ["repl"], obj={})
        assert result.exit_code == 0
        skin = skin_cls.instances[0]
        assert skin.banners == 1
        assert skin.goodbyes == 1
        assert skin.help_arg is cli.commands

    def test_quit_also_exits(self):
        skin_cls = _fake_skin_class(["quit"])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            result = CliRunner().invoke(cli, ["repl"], obj={})
        assert result.exit_code == 0
        assert skin_cls.instances[0].goodbyes == 1

    def test_keyboard_interrupt_says_goodbye(self):
        skin_cls = _fake_skin_class([])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            with patch.object(skin_cls, "get_input", side_effect=KeyboardInterrupt):
                CliRunner().invoke(cli, ["repl"], obj={})
        assert skin_cls.instances[0].goodbyes == 1

    def test_eof_says_goodbye(self):
        skin_cls = _fake_skin_class([])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            with patch.object(skin_cls, "get_input", side_effect=EOFError):
                CliRunner().invoke(cli, ["repl"], obj={})
        assert skin_cls.instances[0].goodbyes == 1

    def test_command_exception_is_reported_via_skin(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        skin_cls = _fake_skin_class(["mqtt pub t p", "exit"])
        with (
            patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls),
            patch.object(cli_mod.mqtt_core, "publish_raw", side_effect=RuntimeError("boom")),
        ):
            monkeypatch.chdir(tmp_path)
            CliRunner().invoke(cli, ["repl"], obj={})
        assert any("boom" in e for e in skin_cls.instances[0].errors)

    def test_multi_token_commands_are_shlexed_and_run(self, tmp_path, monkeypatch):
        _profile(tmp_path, monkeypatch)
        skin_cls = _fake_skin_class(["floors --help", "exit"])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            result = CliRunner().invoke(cli, ["repl"], obj={})
        assert result.exit_code == 0
        assert skin_cls.instances[0].errors == []

    def test_missing_prompt_toolkit_prints_hint(self):
        import sys

        with patch.dict(sys.modules, {"cli_anything.espresense.utils.repl_skin": None}):
            result = CliRunner().invoke(cli, ["repl"], obj={})
        assert result.exit_code == 0
        assert "prompt-toolkit" in result.output

    def test_root_without_subcommand_launches_repl(self):
        skin_cls = _fake_skin_class(["exit"])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            result = CliRunner().invoke(cli, [], obj={})
        assert result.exit_code == 0
        assert skin_cls.instances[0].banners == 1
        assert skin_cls.instances[0].goodbyes == 1


# ────────────────────────────────────────────── REPL skin interactive paths


class TestReplSkinInteractive:
    def test_create_prompt_session_returns_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        session = skin.create_prompt_session()
        assert session is not None

    def test_create_prompt_session_returns_none_without_prompt_toolkit(self, tmp_path):
        import sys

        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        with patch.dict(sys.modules, {"prompt_toolkit": None}):
            assert skin.create_prompt_session() is None

    def test_get_input_uses_prompt_toolkit_session(self, tmp_path):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        fake_session = MagicMock()
        fake_session.prompt.return_value = "  hello  "
        assert skin.get_input(fake_session) == "hello"

    def test_get_input_falls_back_to_input(self, tmp_path, monkeypatch):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        monkeypatch.setattr("builtins.input", lambda *_: "  raw  ")
        assert skin.get_input(None) == "raw"

    def test_bottom_toolbar_builds_formatted_text(self, tmp_path):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        toolbar = skin.bottom_toolbar({"project": "demo", "mode": "edit"})
        rendered = toolbar()
        flat = [text for _style, text in rendered]
        assert "project: " in " ".join(flat)
        assert "demo" in flat

    def test_banner_in_color_and_plain(self, tmp_path, capsys):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        skin._color = True
        skin.print_banner()
        assert _CYAN in capsys.readouterr().out
        skin._color = False
        skin.print_banner()
        captured = capsys.readouterr().out
        assert _CYAN not in captured
        assert "◆" in captured

    def test_plain_prompt_icon_follows_color(self, tmp_path):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        skin._color = True
        assert _CYAN in skin.prompt("esp")
        skin._color = False
        plain = skin.prompt("esp")
        assert plain.startswith("> ")
        assert _CYAN not in plain

    def test_prompt_tokens_carries_context_and_modified_marker(self, tmp_path):
        skin = ReplSkin("espresense", history_file=str(tmp_path / "h.txt"))
        texts = [t for _s, t in skin.prompt_tokens("proj", modified=True, context="ctx")]
        assert "ctx*" in texts


# ────────────────────────────────────────────── stream edge cases


class TestStreamEdgeCases:
    def _mock_websocket(self, recv_side_effect):
        fake_ws = MagicMock()

        if isinstance(recv_side_effect, list):
            it = iter(recv_side_effect)

            def _recv():
                try:
                    return next(it)
                except StopIteration:
                    return ""

            fake_ws.recv.side_effect = _recv
        else:
            fake_ws.recv.side_effect = recv_side_effect
        mock_ws_module = MagicMock()
        mock_ws_module.create_connection.return_value = fake_ws
        mock_ws_module.WebSocketTimeoutException = Exception
        return mock_ws_module, fake_ws

    def test_broken_callback_does_not_abort_collection(self, caplog):
        mock_ws, _ = self._mock_websocket([json.dumps({"type": "deviceChanged", "id": "d1"})])
        calls = []

        def bad_callback(event):
            calls.append(event)
            raise RuntimeError("callback exploded")

        with (
            patch.object(stream_mod, "websocket", mock_ws),
            caplog.at_level(logging.WARNING, logger="cli_anything.espresense.core.stream"),
        ):
            result = stream("http://localhost:8267", duration=0.01, callback=bad_callback)
        assert len(result) == 1
        assert len(calls) == 1
        assert "callback exploded" in caplog.text

    def test_keyboard_interrupt_stops_collection(self):
        mock_ws, fake_ws = self._mock_websocket(KeyboardInterrupt())
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267")
        assert result == []
        fake_ws.close.assert_called_once()

    def test_close_failure_is_swallowed(self):
        mock_ws, fake_ws = self._mock_websocket([json.dumps({"type": "x"})])
        fake_ws.close.side_effect = RuntimeError("already closed")
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267", duration=0.01)
        assert len(result) == 1

    def test_companion_stream_command_passes_filters(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with patch.object(
            cli_mod.stream_core, "stream", return_value=[{"type": "deviceChanged"}]
        ) as mock_stream:
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    cfg,
                    "--json",
                    "companion",
                    "stream",
                    "--duration",
                    "0.01",
                    "--type",
                    "deviceChanged",
                    "--show-all",
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"type": "deviceChanged"}]
        kwargs = mock_stream.call_args[1]
        assert kwargs["duration"] == 0.01
        assert kwargs["types"] == {"deviceChanged"}
        assert kwargs["show_all"] is True


# ────────────────────────────────────────────── mqtt.watch forever + companions


class TestMqttWatchForever:
    def test_no_duration_runs_until_keyboard_interrupt(self):
        fake_client = MagicMock()
        with (
            patch.object(mqtt, "mqtt") as mock_mqtt_lib,
            patch.object(mqtt, "time") as mock_time,
        ):
            mock_mqtt_lib.Client.return_value = fake_client
            mock_time.sleep.side_effect = [None, KeyboardInterrupt]
            result = mqtt.watch("broker.local", "espresense/#")
        assert result == []
        fake_client.subscribe.assert_called_once_with("espresense/#", qos=0)
        fake_client.loop_stop.assert_called_once()
        fake_client.disconnect.assert_called_once()


class TestCompanionClientPostData:
    def test_post_passes_data_payload(self):
        client = CompanionClient("http://localhost:8267")
        fake_resp = MagicMock(status_code=200)
        with patch.object(client.session, "request", return_value=fake_resp) as mock_req:
            client.request("POST", "/api/state/calibration/reset", data=b"raw")
        kwargs = mock_req.call_args[1]
        assert kwargs["data"] == b"raw"

    def test_post_convenience_returns_parsed_body(self):
        client = CompanionClient("http://localhost:8267")
        fake_resp = MagicMock(status_code=200)
        fake_resp.content = b'{"ok": true}'
        fake_resp.headers = {"Content-Type": "application/json"}
        fake_resp.json.return_value = {"ok": True}
        with patch.object(client.session, "request", return_value=fake_resp):
            body = client.post("/api/settings", data="raw=1")
        assert body == {"ok": True}


# ────────────────────────────────────────────── companion edge commands


class TestCompanionEdgeCommands:
    def _cfg(self, tmp_path, monkeypatch):
        return _profile(tmp_path, monkeypatch)

    def test_info_aborts_on_companion_error(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        fake_client = MagicMock()
        fake_client.get.side_effect = CompanionError("connection refused")
        with patch.object(cli_mod, "make_client", return_value=fake_client):
            result = CliRunner().invoke(cli, ["--config", cfg, "companion", "info"])
        assert result.exit_code == 1
        assert "connection refused" in result.output

    def test_config_get_default_renders_yaml(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        fake_client = MagicMock()
        fake_client.get.return_value = {"mqtt": {"host": "broker.local"}}
        with patch.object(cli_mod, "make_client", return_value=fake_client):
            result = CliRunner().invoke(cli, ["--config", cfg, "companion", "config-get"])
        assert result.exit_code == 0
        assert "broker.local" in result.output
        assert "{" not in result.output

    def test_config_fetch_writes_to_file(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        out = tmp_path / "fetched.yaml"
        with (
            patch.object(cli_mod, "make_k8s_target", return_value=object()),
            patch.object(cli_mod.k8s_backend, "read_config", return_value="mqtt:\n  host: h\n"),
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "companion", "config-fetch", "-o", str(out)]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["fetched"] == str(out)
        assert data["bytes"] == len("mqtt:\n  host: h\n")
        assert out.read_text() == "mqtt:\n  host: h\n"

    def test_config_push_restarts_when_asked(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        src = tmp_path / "config.yaml"
        src.write_text(CONFIG_YAML)
        with (
            patch.object(cli_mod, "make_k8s_target", return_value=object()),
            patch.object(cli_mod.k8s_backend, "write_config") as mock_write,
            patch.object(cli_mod.k8s_backend, "restart") as mock_restart,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "companion", "config-push", str(src), "--restart"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["restarted"] is True
        mock_restart.assert_called_once()
        mock_write.assert_called_once()

    def test_restart_waits_for_rollout(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_k8s_target", return_value=object()),
            patch.object(cli_mod.k8s_backend, "restart"),
            patch.object(
                cli_mod.k8s_backend, "rollout_status", return_value="successfully rolled out"
            ),
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "companion", "restart"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["restarted"] is True
        assert "rolled out" in data["rollout"]

    def test_restart_no_wait_skips_rollout(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_k8s_target", return_value=object()),
            patch.object(cli_mod.k8s_backend, "restart"),
            patch.object(cli_mod.k8s_backend, "rollout_status") as mock_rollout,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "companion", "restart", "--no-wait"]
            )
        assert result.exit_code == 0
        assert "rollout" not in json.loads(result.output)
        mock_rollout.assert_not_called()


# ────────────────────────────────────────────── mqtt set-* family


class TestMqttSetFamily:
    def test_set_node_publishes_setting(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        with patch.object(
            cli_mod.mqtt_core,
            "publish_setting",
            return_value={"topic": "espresense/rooms/n1/enabled/set", "payload": "true"},
        ) as mock_pub:
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "mqtt", "set-node", "n1", "enabled", "true"]
            )
        assert result.exit_code == 0
        kwargs = mock_pub.call_args[1]
        assert kwargs["node_id"] == "n1"
        assert kwargs["key"] == "enabled"
        assert kwargs["value"] == "true"
        assert kwargs["retain"] is True

    def test_set_device_bad_json_aborts(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        result = CliRunner().invoke(cli, ["--config", cfg, "mqtt", "set-device", "d1", "{bad json"])
        assert result.exit_code == 1
        assert "not valid JSON" in result.output

    def test_set_device_broker_error_aborts(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        with patch.object(
            cli_mod.mqtt_core,
            "publish_device_config",
            side_effect=MqttError("broker unreachable"),
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "mqtt", "set-device", "d1", '{"name": "x"}']
            )
        assert result.exit_code == 1
        assert "broker unreachable" in result.output

    def test_set_global_broker_error_aborts(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        with patch.object(
            cli_mod.mqtt_core,
            "publish_global_setting",
            side_effect=MqttError("broker unreachable"),
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "mqtt", "set-global", "expiration", "300"]
            )
        assert result.exit_code == 1
        assert "broker unreachable" in result.output

    def test_watch_json_collects_records(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})
        records = [{"topic": "t", "payload": "p", "ts": 1.0}]
        with patch.object(cli_mod.mqtt_core, "watch", return_value=records) as mock_watch:
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "mqtt", "watch", "espresense/#", "--duration", "1"]
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == records
        mock_watch.assert_called_once()

    def test_watch_human_prints_topic_payload(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch, {"mqtt_host": "broker.local"})

        def fake_watch(topic_filter, duration=None, callback=None, **kw):
            callback("espresense/rooms/x/telemetry", "3.5")

        with patch.object(cli_mod.mqtt_core, "watch", side_effect=fake_watch):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "mqtt", "watch", "espresense/#", "--duration", "1"]
            )
        assert result.exit_code == 0
        assert "espresense/rooms/x/telemetry\t3.5" in result.output


# ────────────────────────────────────────────── direct-node command group


class TestNodeDirectCli:
    def _client(self):
        client = MagicMock()
        client.info.return_value = {"name": "n1", "devices": [{"id": "d1"}]}
        client.restart.return_value = True
        client.reboot.return_value = True
        client.get_settings.return_value = {"absorption": "2.8"}
        client.put_settings.return_value = {"saved": True}
        client.rename.return_value = {"renamed": True}
        client.scan_wifi.return_value = [{"ssid": "home"}]
        client.list_device_configs.return_value = [{"id": "d1"}]
        client.upsert_device_config.return_value = {"ok": True}
        client.delete_device_config.return_value = True
        return client

    def _run(self, argv, **kw):
        client = self._client()
        with patch.object(cli_mod.node_direct, "NodeClient", return_value=client):
            result = CliRunner().invoke(cli, list(argv), obj={})
        return result, client

    def test_info(self):
        result, client = self._run(["--json", "node", "info", "10.0.0.5", "--show-all"])
        assert result.exit_code == 0
        client.info.assert_called_once_with(show_all=True)

    def test_restart(self):
        result, client = self._run(["--json", "node", "restart", "10.0.0.5"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"host": "10.0.0.5", "restarted": True}

    def test_settings_read(self):
        result, client = self._run(["--json", "node", "settings", "10.0.0.5", "--section", "main"])
        assert result.exit_code == 0
        client.get_settings.assert_called_once_with("main")

    def test_set_fields(self):
        result, client = self._run(
            ["--json", "node", "set", "10.0.0.5", "absorption=2.8", "tx_ref_rssi=-59"]
        )
        assert result.exit_code == 0
        client.put_settings.assert_called_once_with(
            "extras", {"absorption": "2.8", "tx_ref_rssi": "-59"}
        )

    def test_set_rejects_bad_field(self):
        result, client = self._run(["--json", "node", "set", "10.0.0.5", "absorption"])
        assert result.exit_code == 1
        assert "expected key=value" in result.output

    def test_set_rejects_empty_fields(self):
        result, client = self._run(["--json", "node", "set", "10.0.0.5"])
        assert result.exit_code == 1
        assert "no fields supplied" in result.output

    def test_rename(self):
        result, client = self._run(["--json", "node", "rename", "10.0.0.5", "Kitchen"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"host": "10.0.0.5", "renamed": True}

    def test_scan_wifi(self):
        result, client = self._run(["--json", "node", "scan-wifi", "10.0.0.5"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"ssid": "home"}]

    def test_devices_uses_show_all(self):
        result, client = self._run(["--json", "node", "devices", "10.0.0.5"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": "d1"}]
        client.info.assert_called_once_with(show_all=True)

    def test_reboot(self):
        result, client = self._run(["--json", "node", "reboot", "10.0.0.5"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"host": "10.0.0.5", "rebooted": True}

    def test_config_list(self):
        result, client = self._run(["--json", "node", "config-list", "10.0.0.5"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": "d1"}]

    def test_config_set(self):
        result, client = self._run(
            [
                "--json",
                "node",
                "config-set",
                "10.0.0.5",
                "d1",
                "--name",
                "Watch",
                "--rssi-at-1m",
                "-59",
            ]
        )
        assert result.exit_code == 0
        client.upsert_device_config.assert_called_once_with(
            "d1", alias=None, name="Watch", rssi_at_1m=-59
        )

    def test_config_set_requires_a_field(self):
        result, client = self._run(["--json", "node", "config-set", "10.0.0.5", "d1"])
        assert result.exit_code == 1
        assert "nothing to set" in result.output

    def test_config_delete(self):
        result, client = self._run(["--json", "node", "config-delete", "10.0.0.5", "d1"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"host": "10.0.0.5", "device_id": "d1", "deleted": True}


# ────────────────────────────────────────────── calibration CLI bodies


class TestCalibrationCli:
    def test_get_and_summary(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(cli_mod.calibration_core, "get", return_value={"r": 0.9}) as mock_get,
            patch.object(
                cli_mod.calibration_core, "summary", return_value={"rmse": 0.4}
            ) as mock_summary,
        ):
            runner = CliRunner()
            r1 = runner.invoke(cli, ["--config", cfg, "--json", "calibration", "get"])
            r2 = runner.invoke(cli, ["--config", cfg, "--json", "calibration", "summary"])
        assert r1.exit_code == 0
        assert json.loads(r1.output) == {"r": 0.9}
        assert r2.exit_code == 0
        assert json.loads(r2.output) == {"rmse": 0.4}
        mock_get.assert_called_once()
        mock_summary.assert_called_once()

    def test_reset_runs_with_yes(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(
                cli_mod.calibration_core, "reset", return_value={"reset": True}
            ) as mock_reset,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "calibration", "reset", "--yes"]
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == {"reset": True}
        mock_reset.assert_called_once()

    def test_auto_optimize_off(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(
                cli_mod.calibration_core, "auto_optimize_set", return_value={"enabled": False}
            ) as mock_set,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "calibration", "auto-optimize", "off"]
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == {"enabled": False}
        mock_set.assert_called_once_with(fake_client, False)


# ────────────────────────────────────────────── devices runtime set/delete


class TestDevicesRuntimeSetDelete:
    def test_set_updates_fields(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(
                cli_mod.devices_core,
                "update_device",
                return_value={"id": "d1", "name": "New"},
            ) as mock_upd,
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    cfg,
                    "--json",
                    "devices",
                    "set",
                    "d1",
                    "--name",
                    "New",
                    "--ref-rssi",
                    "-60",
                ],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["device_id"] == "d1"
        kwargs = mock_upd.call_args[1]
        assert kwargs["name"] == "New"
        assert kwargs["ref_rssi"] == -60

    def test_set_with_no_fields_reports_it(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_client", return_value=MagicMock()),
            patch.object(cli_mod.devices_core, "update_device", return_value=None),
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "devices", "set", "d1"])
        assert result.exit_code == 0
        assert json.loads(result.output)["result"] == "no fields"

    def test_delete_confirms_then_deletes(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(cli_mod.devices_core, "delete_device") as mock_del,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "devices", "delete", "d1", "--yes"]
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == {"device_id": "d1", "deleted": True}
        mock_del.assert_called_once_with(fake_client, "d1")


# ────────────────────────────────────────────── coordinate parse aborts


class TestCoordinateParseAborts:
    def test_rooms_set_points_rejects_non_numeric_xy(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            cli, ["--config", cfg, "rooms", "set-points", "Office", "--point", "1,abc"]
        )
        assert result.exit_code == 1
        assert "coordinates must be numbers" in result.output

    def test_rooms_set_points_rejects_wrong_arity(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            cli, ["--config", cfg, "rooms", "set-points", "Office", "--point", "1"]
        )
        assert result.exit_code == 1
        assert "expected x,y" in result.output

    def test_floors_set_bounds_rejects_non_numeric_xyz(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            cli, ["--config", cfg, "floors", "set-bounds", "gf", "1,2,z", "3,4,5"]
        )
        assert result.exit_code == 1
        assert "coordinates must be numbers" in result.output


# ────────────────────────────────────────────── settings error paths


@pytest.fixture()
def file_env(tmp_path, monkeypatch):
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")

    def run(*args, json_out=True):
        argv = ["--config", str(profile)]
        if json_out:
            argv.append("--json")
        argv.extend(args)
        argv.extend(["--file", str(cfg)])
        return CliRunner().invoke(cli, argv)

    return run


class TestSettingsErrorPaths:
    def test_show_unknown_section_aborts(self, file_env):
        result = file_env("settings", "show", "--section", "nope")
        assert result.exit_code == 1

    def test_get_unknown_path_exits_one(self, file_env):
        result = file_env("settings", "get", "nope.nada")
        assert result.exit_code == 1
        assert json.loads(result.output)["found"] is False

    def test_set_into_structural_block_is_refused(self, file_env):
        result = file_env("settings", "set", "floors.0.id", "x")
        assert result.exit_code == 1
        assert "floors" in result.output

    def test_unset_missing_path_exits_one(self, file_env):
        result = file_env("settings", "unset", "nope.nada")
        assert result.exit_code == 1


# ────────────────────────────────────────────── batch 2: remaining branches


class TestCompanionFetchWithoutOut:
    def test_config_fetch_to_stdout(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_k8s_target", return_value=object()),
            patch.object(cli_mod.k8s_backend, "read_config", return_value="mqtt:\n  host: h\n"),
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "companion", "config-fetch"])
        assert result.exit_code == 0
        assert "host: h" in result.output


class TestNodesRuntimeCommands:
    def test_show_reads_the_companion_api(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(cli_mod.companion_api, "get_node", return_value={"id": "n1"}) as mock_get,
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "nodes", "show", "n1"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"id": "n1"}
        mock_get.assert_called_once_with(fake_client, "n1")

    def test_list_falls_back_to_config_when_api_unreachable(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        yaml_cfg = tmp_path / "config.yaml"
        yaml_cfg.write_text(CONFIG_YAML)
        fake_client = MagicMock()
        fake_client.get.side_effect = CompanionError("api down")
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(
                cli_mod.companion_api, "list_nodes", side_effect=CompanionError("api down")
            ),
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "nodes", "list", "--file", str(yaml_cfg)]
            )
        assert result.exit_code == 0
        assert "live API unreachable" in result.output
        rows = json.loads(result.output.split("\n", 1)[1])
        assert [r["name"] for r in rows] == ["office-node"]

    def test_restart_sends_the_api_command(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(
                cli_mod.companion_api, "restart_node", return_value={"ok": True}
            ) as mock_restart,
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "nodes", "restart", "n1"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "node_id": "n1",
            "restart": "sent",
            "response": {"ok": True},
        }
        mock_restart.assert_called_once_with(fake_client, "n1")

    def test_update_firmware_triggers_ota(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_client", return_value=MagicMock()),
            patch.object(cli_mod.companion_api, "update_node_firmware") as mock_upd,
        ):
            result = CliRunner().invoke(
                cli,
                ["--config", cfg, "--json", "nodes", "update-firmware", "n1", "http://fw/url"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["update_triggered"] is True
        mock_upd.assert_called_once()

    def test_put_settings_pushes_the_blob(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_client", return_value=MagicMock()),
            patch.object(cli_mod.companion_api, "put_node") as mock_put,
        ):
            result = CliRunner().invoke(
                cli, ["--config", cfg, "--json", "nodes", "put-settings", "n1", '{"id": "n1"}']
            )
        assert result.exit_code == 0
        assert json.loads(result.output) == {"node_id": "n1", "updated": True}
        mock_put.assert_called_once()

    def test_put_settings_rejects_bad_json(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        result = CliRunner().invoke(cli, ["--config", cfg, "nodes", "put-settings", "n1", "{bad"])
        assert result.exit_code == 1
        assert "not valid JSON" in result.output


class TestNodesNotFoundBranches:
    """not-found in config.yaml must skip the push (and still emit)."""

    def test_rename_in_config_unknown_node_pushes_nothing(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        yaml_cfg = tmp_path / "config.yaml"
        yaml_cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ) as mock_push:
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    cfg,
                    "--json",
                    "nodes",
                    "rename-in-config",
                    "ghost",
                    "new",
                    "--file",
                    str(yaml_cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["found"] is False
        mock_push.assert_not_called()

    def test_set_point_unknown_node_pushes_nothing(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        yaml_cfg = tmp_path / "config.yaml"
        yaml_cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ) as mock_push:
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    cfg,
                    "--json",
                    "nodes",
                    "set-point",
                    "ghost",
                    "1",
                    "2",
                    "3",
                    "--file",
                    str(yaml_cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["found"] is False
        mock_push.assert_not_called()


class TestDevicesRuntimeListShow:
    def test_list(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        with (
            patch.object(cli_mod, "make_client", return_value=MagicMock()),
            patch.object(
                cli_mod.devices_core,
                "list_devices",
                return_value=[{"id": "d1", "name": "Phone"}],
            ) as mock_list,
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "devices", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": "d1", "name": "Phone"}]
        mock_list.assert_called_once()

    def test_show(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        fake_client = MagicMock()
        with (
            patch.object(cli_mod, "make_client", return_value=fake_client),
            patch.object(cli_mod.devices_core, "get_device", return_value={"id": "d1"}) as mock_get,
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "devices", "show", "d1"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"id": "d1"}
        mock_get.assert_called_once_with(fake_client, "d1")


class TestToggleStatusUnknownName:
    def test_locator_status_unknown_aborts(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        result = CliRunner().invoke(
            cli,
            ["--config", str(profile), "settings", "locator", "nope", "status", "--file", str(cfg)],
        )
        assert result.exit_code == 1
        assert "no locators.nope" in result.output


class TestHistoryWithoutLimit:
    def test_no_limit_returns_all_rows(self, tmp_path, monkeypatch):
        cfg = _profile(tmp_path, monkeypatch)
        rows = [{"ts": f"t{i}"} for i in range(5)]
        with (
            patch.object(cli_mod, "make_client", return_value=MagicMock()),
            patch.object(cli_mod.history_core, "get_history", return_value=rows),
        ):
            result = CliRunner().invoke(cli, ["--config", cfg, "--json", "history", "get", "d1"])
        assert result.exit_code == 0
        assert len(json.loads(result.output)) == 5


class TestDryRunAndNoOpPushBranches:
    def test_floors_retag_same_id_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "floors",
                    "retag",
                    "gf",
                    "gf",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["id_changed"] is False

    def test_floors_fit_bounds_dry_run_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "floors",
                    "fit-bounds",
                    "gf",
                    "--dry-run",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True

    def test_rooms_rotate_dry_run_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "rooms",
                    "rotate",
                    "--map",
                    "Office=Desk",
                    "--dry-run",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True

    def test_rooms_repoint_node_unknown_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "rooms",
                    "repoint-node",
                    "ghost-node",
                    "Office",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["found"] is False

    def test_rooms_set_points_dry_run_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "rooms",
                    "set-points",
                    "Office",
                    "--point",
                    "0,0",
                    "--point",
                    "5,0",
                    "--point",
                    "5,4",
                    "--point",
                    "0,4",
                    "--dry-run",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True

    def test_rooms_scale_dry_run_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "rooms",
                    "scale",
                    "Office",
                    "1.5",
                    "--dry-run",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True

    def test_rooms_set_color_dry_run_pushes_nothing(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile.json"
        profile.write_text("{}")
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML)
        with patch.object(
            cli_mod.config_source_core.FileSource,
            "push",
            side_effect=AssertionError("must not push"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(profile),
                    "--json",
                    "rooms",
                    "set-color",
                    "Office",
                    "#ff0000",
                    "--dry-run",
                    "--file",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True
