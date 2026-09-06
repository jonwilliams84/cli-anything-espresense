"""Error-path refinement pass.

Every mutating/reading command in espresense_cli.py funnels failures through
`_abort` (stderr message + exit 1, never a traceback). The happy paths were
covered by the earlier suites; this file pins the *failure* contract — one test
per guard the CLI promises its users (and its agents).

Also covers the last bare spots in core/: the thin calibration wrappers,
FileSource backup failure, kubectl-missing RuntimeError, node-direct HTTP error
branches, rotation validation, telemetry topic prefixing, doctor's defensive
locator fallback, the CompanionClient transport wrapper, colour auto-detect,
and `python -m cli_anything.espresense` itself.
"""

from __future__ import annotations

import json
import runpy
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from cli_anything.espresense import espresense_cli as cli_mod
from cli_anything.espresense.core import (
    calibration,
    config_source,
    devices,
    k8s_backend,
    node_direct,
    rooms as rooms_core,
    telemetry as telemetry_core,
    validate as validate_core,
)
from cli_anything.espresense.core.node_direct import NodeError
from cli_anything.espresense.espresense_cli import cli
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError


CONFIG_YAML = """\
locators:
  nelder_mead:
    enabled: true
floors:
  - id: gf
    name: Ground Floor
    bounds: [[0, 0, 0], [10, 10, 3]]
    rooms:
      - name: Office
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
nodes:
  - name: office-node
    room: Office
    point: [1.0, 2.0, 2.5]
  - name: naked-node
devices:
  - id: "irk:one"
    name: Phone
    "rssi@1m": -65
  - id: "irk:two"
    name: Keys
    "rssi@1m": -70
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """CliRunner bound to an isolated profile and a real config.yaml."""
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")

    def run(*args, json_out=True, file=True):
        argv = ["--config", str(profile)]
        if json_out:
            argv.append("--json")
        argv.extend([*args])
        if file:
            argv.extend(["--file", str(cfg)])
        return CliRunner().invoke(cli, argv)

    run.cfg = cfg  # type: ignore[attr-defined]
    run.profile = profile  # type: ignore[attr-defined]
    return run


def assert_aborted(result, *fragments):
    """The CLI failure contract: exit 1, `error:` on stderr, message, no traceback."""
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    for frag in fragments:
        assert frag in result.output, f"{frag!r} missing from: {result.output}"


# ─────────────────────────────── coordinate option parsers


class TestCoordinateParsing:
    def test_parse_xy_rejects_non_numeric_coordinates(self, env):
        res = env("rooms", "set-points", "Office", "--point", "a,b")
        assert_aborted(res, "coordinates must be numbers")

    def test_parse_xyz_rejects_non_numeric_coordinates(self, env):
        res = env("floors", "set-bounds", "gf", "0,0,0", "1,2,z")
        assert_aborted(res, "coordinates must be numbers")


# ─────────────────────────────── companion group


class TestCompanionErrorPaths:
    def test_info_aborts_when_the_api_is_unreachable(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.companion_api, "get_config", side_effect=CompanionError("boom")
            ):
                res = env("companion", "info", file=False)
        assert_aborted(res, "boom")

    def test_settings_get_aborts_on_transport_error(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.global_settings_core, "fetch", side_effect=CompanionError("refused")
            ):
                res = env("companion", "settings-get", file=False)
        assert_aborted(res, "refused")

    def test_settings_get_aborts_on_settings_error(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.global_settings_core,
                "fetch",
                side_effect=cli_mod.global_settings_core.GlobalSettingsError("bad key"),
            ):
                res = env("companion", "settings-get", file=False)
        assert_aborted(res, "bad key")

    def test_settings_set_aborts_on_transport_error(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.global_settings_core, "update", side_effect=CompanionError("refused")
            ):
                res = env("companion", "settings-set", "expiration", "300", file=False)
        assert_aborted(res, "refused")

    def test_settings_set_aborts_on_settings_error(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.global_settings_core,
                "update",
                side_effect=cli_mod.global_settings_core.GlobalSettingsError("nope"),
            ):
                res = env("companion", "settings-set", "expiration", "300", file=False)
        assert_aborted(res, "nope")


# ─────────────────────────────── floors group


class TestFloorsErrorPaths:
    def test_show_unknown_floor(self, env):
        res = env("floors", "show", "nope")
        assert_aborted(res, "nope")

    def test_add_duplicate_floor_id(self, env):
        res = env("floors", "add", "gf", "--name", "Again")
        assert_aborted(res, "already exists")

    def test_rename_unknown_floor(self, env):
        res = env("floors", "rename", "nope", "X")
        assert_aborted(res, "nope")

    def test_retag_unknown_floor(self, env):
        res = env("floors", "retag", "nope", "new")
        assert_aborted(res, "nope")

    def test_set_bounds_unknown_floor(self, env):
        res = env("floors", "set-bounds", "nope", "0,0,0", "1,1,1")
        assert_aborted(res, "nope")

    def test_fit_bounds_unknown_floor(self, env):
        res = env("floors", "fit-bounds", "nope")
        assert_aborted(res, "nope")


# ─────────────────────────────── rooms group


class TestRoomsErrorPaths:
    def test_add_duplicate_room_name(self, env):
        res = env(
            "rooms", "add", "gf", "Office", "--point", "5,5", "--point", "6,5", "--point", "6,6"
        )
        assert_aborted(res, "already exists")

    def test_set_points_unknown_room(self, env):
        res = env(
            "rooms", "set-points", "Nope", "--point", "0,0", "--point", "1,0", "--point", "0,1"
        )
        assert_aborted(res, "Nope")

    def test_move_unknown_room(self, env):
        res = env("rooms", "move", "Nope", "1", "1")
        assert_aborted(res, "Nope")

    def test_scale_unknown_room(self, env):
        res = env("rooms", "scale", "Nope", "2")
        assert_aborted(res, "Nope")

    def test_set_color_unknown_room(self, env):
        res = env("rooms", "set-color", "Nope", "red")
        assert_aborted(res, "Nope")


# ─────────────────────────────── nodes group


class TestNodesErrorPaths:
    def test_list_config_only_branch(self, env):
        """--no-merge-live skips the API entirely and shows the config rows."""
        res = env("nodes", "list", "--no-merge-live")
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)
        names = [n["name"] for n in data]
        assert "office-node" in names and "naked-node" in names

    def test_place_into_unknown_room(self, env):
        res = env("nodes", "place", "office-node", "--room", "NoSuite")
        assert_aborted(res, "NoSuite")

    def test_put_settings_rejects_invalid_json(self, env):
        res = env("nodes", "put-settings", "office-node", "{not json", file=False)
        assert_aborted(res, "not valid JSON")

    def test_add_duplicate_node_name(self, env):
        res = env("nodes", "add", "office-node")
        assert_aborted(res, "office-node")


# ─────────────────────────────── devices group


class TestDevicesErrorPaths:
    def test_add_to_config_rejects_duplicate_id(self, env):
        res = env("devices", "add-to-config", "irk:one", "--name", "Again")
        assert_aborted(res, "irk:one")

    def test_update_in_config_rejects_id_collision(self, env):
        res = env("devices", "update-in-config", "irk:one", "--new-id", "irk:two")
        assert_aborted(res, "already exists")

    def test_whereis_aborts_on_telemetry_error(self, env):
        with patch.object(cli_mod, "make_client", return_value=MagicMock()):
            with patch.object(
                cli_mod.telemetry_core, "whereis", side_effect=telemetry_core.TelemetryError("bad")
            ):
                res = env("devices", "whereis", "irk:one", file=False)
        assert_aborted(res, "bad")


# ─────────────────────────────── settings group


class TestSettingsErrorPaths:
    def test_get_of_a_missing_path_exits_one_with_found_false(self, env):
        res = env("settings", "get", "nodes.0.room")
        assert res.exit_code == 1
        assert json.loads(res.output)["found"] is False

    def test_get_aborts_when_core_raises(self, env):
        with patch.object(
            cli_mod.settings_core,
            "get_path",
            side_effect=cli_mod.settings_core.SettingsError("rot"),
        ):
            res = env("settings", "get", "anything")
        assert_aborted(res, "rot")

    def test_set_rejects_structural_path(self, env):
        res = env("settings", "set", "nodes.0.room", "Office")
        assert_aborted(res, "nodes` commands")

    def test_unset_rejects_structural_path(self, env):
        res = env("settings", "unset", "nodes.0.room")
        assert_aborted(res, "nodes` commands")

    def test_locator_status_of_unknown_name(self, env):
        res = env("settings", "locator", "nope", "status")
        assert_aborted(res, "locators.nope")

    def test_locator_on_unknown_name(self, env):
        res = env("settings", "locator", "nope", "on")
        assert_aborted(res, "locators.nope")


# ─────────────────────────────── mqtt group


class TestMqttErrorPaths:
    def _profile_with_broker(self, env):
        env.profile.write_text(json.dumps({"mqtt_host": "broker.local"}))
        return ["--config", str(env.profile), "--json"]

    def test_set_device_rejects_invalid_json(self, env):
        res = CliRunner().invoke(
            cli, [*self._profile_with_broker(env), "mqtt", "set-device", "d1", "{bad"]
        )
        assert_aborted(res, "not valid JSON")

    def test_set_device_aborts_on_mqtt_error(self, env):
        with patch.object(
            cli_mod.mqtt_core,
            "publish_device_config",
            side_effect=cli_mod.mqtt_core.MqttError("down"),
        ):
            res = CliRunner().invoke(
                cli, [*self._profile_with_broker(env), "mqtt", "set-device", "d1", "{}"]
            )
        assert_aborted(res, "down")

    def test_set_global_aborts_on_mqtt_error(self, env):
        with patch.object(
            cli_mod.mqtt_core,
            "publish_global_setting",
            side_effect=cli_mod.mqtt_core.MqttError("down"),
        ):
            res = CliRunner().invoke(
                cli, [*self._profile_with_broker(env), "mqtt", "set-global", "expiration", "300"]
            )
        assert_aborted(res, "down")


# ─────────────────────────────── REPL: unexpected command crashes are reported


class TestReplErrorReporting:
    def _skin_class(self, script):
        from cli_anything.espresense.tests.test_refine_hardening import _fake_skin_class

        return _fake_skin_class(script)

    def test_generic_command_exception_is_reported_not_raised(self):
        """A command that blows up mid-run surfaces via skin.error, then the loop lives on."""
        skin_cls = self._skin_class(["companion info", "exit"])
        with patch("cli_anything.espresense.utils.repl_skin.ReplSkin", skin_cls):
            with patch.object(cli_mod, "make_client", side_effect=RuntimeError("kaboom")):
                result = CliRunner().invoke(cli, ["repl"], obj={})
        assert result.exit_code == 0
        skin = skin_cls.instances[0]
        assert skin.goodbyes == 1
        assert any("kaboom" in e for e in skin.errors), skin.errors


# ─────────────────────────────── core gaps


class TestCalibrationWrappers:
    def test_get_reset_and_auto_optimize_hit_their_endpoints(self):
        client = MagicMock()
        client.get.return_value = {"r": 0.9}
        client.post.return_value = {"ok": True}
        assert calibration.get(client) == {"r": 0.9}
        client.get.assert_called_once_with("/api/state/calibration")
        assert calibration.reset(client) == {"ok": True}
        client.post.assert_called_once_with("/api/state/calibration/reset")
        assert calibration.auto_optimize_get(client) == {"r": 0.9}
        client.get.assert_called_with("/api/state/calibration/auto-optimize")


class TestFileSourceBackupFailure:
    def test_unreadable_source_config_fails_the_backup_loudly(self, tmp_path):
        """A config path that exists but cannot be read (a directory) must
        raise ConfigSourceError, not silently skip the backup."""
        target = tmp_path / "config.yaml"
        target.mkdir()
        src = config_source.FileSource(path=target)
        with pytest.raises(config_source.ConfigSourceError, match="backup"):
            src.push({"floors": []})


class TestKubectlMissing:
    def test_missing_kubectl_names_the_fix(self):
        with patch("cli_anything.espresense.core.k8s_backend.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="kubectl not found"):
                k8s_backend._kubectl()


class TestNodeDirectHttpErrors:
    def _client(self):
        c = node_direct.NodeClient("192.168.1.10")
        return c

    def test_put_settings_raises_on_http_error(self):
        c = self._client()
        resp = MagicMock(status_code=500, text="boom")
        with patch.object(c, "_request", return_value=resp):
            with pytest.raises(NodeError, match="500"):
                c.put_settings("main", {"ssid": "x", "password": "y"})

    def test_upsert_device_config_tolerates_a_non_json_body(self):
        c = self._client()
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("no json")
        resp.text = "ok"
        with patch.object(c, "_request", return_value=resp):
            assert c.upsert_device_config("d1", name="D") == {"raw": "ok"}


class TestRoomsCoreValidation:
    def test_rotate_rejects_duplicate_new_values(self):
        parsed = {"floors": [], "nodes": []}
        with pytest.raises(ValueError, match="duplicate"):
            rooms_core.rotate(parsed, {"A": "B", "C": "B"})

    def test_rename_skips_nodes_without_a_room(self):
        parsed = {
            "floors": [
                {
                    "id": "gf",
                    "rooms": [{"name": "Office", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]}],
                }
            ],
            "nodes": [{"name": "naked-node"}, {"name": "office-node", "room": "Office"}],
        }
        out = rooms_core.rename(parsed, "Office", "Suite")
        assert out["nodes_repointed"] == 1
        assert out["whitespace_fixes"] == 0
        assert "room" not in parsed["nodes"][0]


class TestTelemetryTopicHelpers:
    def test_strip_prefix_of_the_bare_prefix_returns_empty_suffix(self):
        assert telemetry_core._strip_prefix("espresense", "espresense") == ""


class TestValidateDefensivePaths:
    def test_room_without_a_name_is_skipped_not_fatal(self):
        parsed = {
            "floors": [{"id": "gf", "rooms": [{"points": [[0, 0], [1, 0], [1, 1]]}]}],
            "nodes": [],
        }
        out = validate_core.check(parsed)
        assert isinstance(out, dict)
        assert "errors" in out and "warnings" in out

    def test_locator_check_falls_back_to_no_findings_when_toggles_are_unreadable(self):
        parsed = {"locators": {"nelder_mead": {"enabled": True}}}
        from cli_anything.espresense.core import settings as settings_core

        with patch.object(
            settings_core, "list_toggles", side_effect=settings_core.SettingsError("rot")
        ):
            out = validate_core.check(parsed)
        codes = [f["code"] for f in out["errors"] + out["warnings"]]
        assert validate_core.NO_LOCATOR_ENABLED not in codes


class TestCompanionClientTransport:
    def test_request_wraps_request_exceptions_as_companion_errors(self):
        client = CompanionClient()
        with patch.object(
            client.session, "request", side_effect=requests.ConnectionError("no route")
        ):
            with pytest.raises(CompanionError, match="no route"):
                client.request("GET", "/api/config")


class TestDevicesWrapper:
    def test_get_device_hits_the_device_endpoint(self):
        client = MagicMock()
        client.get.return_value = {"id": "d1"}
        assert devices.get_device(client, "d1") == {"id": "d1"}


class TestReplSkinColorDetection:
    def test_tty_stdout_with_colour_enables_colour(self, monkeypatch):
        from cli_anything.espresense.utils.repl_skin import ReplSkin

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin.__new__(ReplSkin)
        fake_stdout = MagicMock()
        fake_stdout.isatty.return_value = True
        with patch("cli_anything.espresense.utils.repl_skin.sys.stdout", fake_stdout):
            assert skin._detect_color_support() is True


class TestPythonDashM:
    def test_module_entry_point_imports_and_defers_to_main(self):
        """`python -m cli_anything.espresense` must reach main(); run it with
        main stubbed so no REPL starts in-process."""
        with patch("cli_anything.espresense.espresense_cli.main") as fake_main:
            runpy.run_path(
                "cli_anything/espresense/__main__.py",
                run_name="__main__",
            )
        assert fake_main.called
