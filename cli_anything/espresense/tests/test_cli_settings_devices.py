"""E2E CLI tests for the config-side `devices` commands and the `settings` group.

Every test here drives the real CLI against a real config.yaml on disk via
`--file`, so nothing is mocked: the assertions are about what ends up in the
YAML and what the command prints.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything.espresense.espresense_cli import cli

CONFIG_YAML = """\
# ESPresense companion config
timeout: 30
away_timeout: 120
mqtt:
  host: broker.local
  port: 1883
  username: espresense
  password: hunter2
locators:
  nadaraya_watson:
    enabled: true
    bandwidth: 0.7
  nelder_mead:
    enabled: false
optimizers:
  absorption:
    enabled: true
devices:
  - id: "irk:aaa"
    name: Phone
    "rssi@1m": -65
  - id: tile-1
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
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

    run.cfg = cfg  # type: ignore[attr-defined]
    return run


def payload(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ───────────────────────────────────────────── devices in config.yaml


class TestDevicesInConfig:
    def test_list(self, env):
        rows = payload(env("devices", "list-in-config"))
        assert [r["id"] for r in rows] == ["irk:aaa", "tile-1"]

    def test_list_reports_reference_rssi(self, env):
        assert payload(env("devices", "list-in-config"))[0]["rssi_at_1m"] == -65.0

    def test_list_table_output(self, env):
        res = env("devices", "list-in-config", json_out=False)
        assert res.exit_code == 0
        assert "irk:aaa" in res.output

    def test_show(self, env):
        assert payload(env("devices", "show-in-config", "tile-1"))["id"] == "tile-1"

    def test_show_unknown_exits_one(self, env):
        res = env("devices", "show-in-config", "ghost")
        assert res.exit_code == 1
        assert "no device" in res.output

    def test_add_writes_the_file(self, env):
        out = payload(env("devices", "add-to-config", "beacon-9", "--name", "Beacon"))
        assert out["added"] is True
        assert "beacon-9" in env.cfg.read_text()

    def test_add_reports_the_push(self, env):
        out = payload(env("devices", "add-to-config", "beacon-9"))
        assert out["pushed"]["source"] == "file"

    def test_add_with_reference_rssi(self, env):
        env("devices", "add-to-config", "beacon-9", "--rssi-at-1m", "-70")
        rows = {r["id"]: r for r in payload(env("devices", "list-in-config"))}
        assert rows["beacon-9"]["rssi_at_1m"] == -70.0

    def test_add_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        out = payload(env("devices", "add-to-config", "beacon-9", "--dry-run"))
        assert out["dry_run"] is True
        assert env.cfg.read_text() == before

    def test_add_duplicate_exits_one(self, env):
        res = env("devices", "add-to-config", "tile-1")
        assert res.exit_code == 1
        assert "already exists" in res.output

    def test_update_name(self, env):
        out = payload(env("devices", "update-in-config", "tile-1", "--name", "Keys"))
        assert out["changed"] == ["name"]
        assert "Keys" in env.cfg.read_text()

    def test_update_rssi(self, env):
        payload(env("devices", "update-in-config", "tile-1", "--rssi-at-1m", "-72"))
        rows = {r["id"]: r for r in payload(env("devices", "list-in-config"))}
        assert rows["tile-1"]["rssi_at_1m"] == -72.0

    def test_update_new_id(self, env):
        payload(env("devices", "update-in-config", "tile-1", "--new-id", "keys"))
        assert [r["id"] for r in payload(env("devices", "list-in-config"))] == ["irk:aaa", "keys"]

    def test_update_clear_name(self, env):
        out = payload(env("devices", "update-in-config", "irk:aaa", "--clear-name"))
        assert out["after"]["name"] is None

    def test_update_clear_rssi(self, env):
        out = payload(env("devices", "update-in-config", "irk:aaa", "--clear-rssi"))
        assert out["after"]["rssi_at_1m"] is None

    def test_update_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("devices", "update-in-config", "tile-1", "--name", "Keys", "--dry-run"))
        assert env.cfg.read_text() == before

    def test_update_unknown_exits_one(self, env):
        res = env("devices", "update-in-config", "ghost", "--name", "X")
        assert res.exit_code == 1

    def test_update_with_no_fields_exits_one(self, env):
        res = env("devices", "update-in-config", "tile-1")
        assert res.exit_code == 1
        assert "nothing to change" in res.output

    def test_update_conflicting_name_flags_exit_one(self, env):
        res = env("devices", "update-in-config", "tile-1", "--name", "X", "--clear-name")
        assert res.exit_code == 1

    def test_update_conflicting_rssi_flags_exit_one(self, env):
        res = env("devices", "update-in-config", "tile-1", "--rssi-at-1m", "-1", "--clear-rssi")
        assert res.exit_code == 1

    def test_update_id_collision_exits_one(self, env):
        res = env("devices", "update-in-config", "tile-1", "--new-id", "irk:aaa")
        assert res.exit_code == 1
        assert "already exists" in res.output

    def test_remove(self, env):
        out = payload(env("devices", "remove-from-config", "tile-1"))
        assert out["removed"] is True
        assert "tile-1" not in env.cfg.read_text()

    def test_remove_dry_run_keeps_the_entry(self, env):
        payload(env("devices", "remove-from-config", "tile-1", "--dry-run"))
        assert "tile-1" in env.cfg.read_text()

    def test_remove_unknown_exits_one(self, env):
        res = env("devices", "remove-from-config", "ghost")
        assert res.exit_code == 1
        assert json.loads(res.output)["removed"] is False

    def test_a_missing_config_file_is_reported(self, tmp_path):
        res = CliRunner().invoke(
            cli, ["--json", "devices", "list-in-config", "--file", str(tmp_path / "nope.yaml")]
        )
        assert res.exit_code != 0


# ───────────────────────────────────────────── settings


class TestSettingsShow:
    def test_lists_tuning_keys(self, env):
        out = payload(env("settings", "show"))
        assert out["timeout"] == 30

    def test_redacts_the_broker_password(self, env):
        assert payload(env("settings", "show"))["mqtt"]["password"] == "***"

    def test_reveal_shows_it(self, env):
        assert payload(env("settings", "show", "--reveal"))["mqtt"]["password"] == "hunter2"

    def test_structural_blocks_are_summarised(self, env):
        assert payload(env("settings", "show"))["nodes"] == "<1 entry>"

    def test_section_filter(self, env):
        assert set(payload(env("settings", "show", "--section", "mqtt"))) == {"mqtt"}

    def test_unknown_section_exits_one(self, env):
        assert env("settings", "show", "--section", "ghost").exit_code == 1

    def test_text_output(self, env):
        res = env("settings", "show", json_out=False)
        assert res.exit_code == 0
        assert "timeout: 30" in res.output

    def test_text_output_still_redacts(self, env):
        assert "hunter2" not in env("settings", "show", json_out=False).output


class TestSettingsGet:
    def test_reads_a_scalar(self, env):
        assert payload(env("settings", "get", "timeout"))["value"] == 30

    def test_reads_a_nested_path(self, env):
        out = payload(env("settings", "get", "locators.nadaraya_watson.bandwidth"))
        assert out["value"] == 0.7

    def test_missing_path_exits_one(self, env):
        res = env("settings", "get", "locators.ghost.enabled")
        assert res.exit_code == 1
        assert json.loads(res.output)["found"] is False

    def test_secret_is_redacted(self, env):
        assert payload(env("settings", "get", "mqtt.password"))["value"] == "***"

    def test_reveal_opt_in(self, env):
        assert payload(env("settings", "get", "mqtt.password", "--reveal"))["value"] == "hunter2"

    def test_empty_path_exits_one(self, env):
        assert env("settings", "get", "  ").exit_code == 1


class TestSettingsSet:
    def test_writes_a_scalar(self, env):
        out = payload(env("settings", "set", "away_timeout", "300"))
        assert (out["before"], out["after"]) == (120, 300)
        assert "away_timeout: 300" in env.cfg.read_text()

    def test_writes_a_bool(self, env):
        payload(env("settings", "set", "locators.nelder_mead.enabled", "true"))
        rows = {r["name"]: r for r in payload(env("settings", "locators"))}
        assert rows["nelder_mead"]["enabled"] is True

    def test_creates_a_new_block(self, env):
        out = payload(env("settings", "set", "locators.gauss_newton.enabled", "true"))
        assert out["created"] == ["locators.gauss_newton"]

    def test_type_override(self, env):
        payload(env("settings", "set", "mqtt.host", "true", "--type", "str"))
        assert payload(env("settings", "get", "mqtt.host"))["value"] == "true"

    def test_bad_typed_value_exits_one(self, env):
        assert env("settings", "set", "timeout", "soon", "--type", "int").exit_code == 1

    def test_structural_path_is_refused(self, env):
        res = env("settings", "set", "nodes.0.room", "Kitchen")
        assert res.exit_code == 1
        assert "nodes" in res.output

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("settings", "set", "timeout", "45", "--dry-run"))
        assert env.cfg.read_text() == before

    def test_secret_write_is_masked_in_the_output(self, env):
        out = payload(env("settings", "set", "mqtt.password", "newpass"))
        assert out["after"] == "***"
        assert "newpass" in env.cfg.read_text()

    def test_comments_survive_the_edit(self, env):
        payload(env("settings", "set", "timeout", "45"))
        assert env.cfg.read_text().startswith("# ESPresense companion config")


class TestSettingsUnset:
    def test_removes_a_key(self, env):
        out = payload(env("settings", "unset", "away_timeout"))
        assert out["removed"] is True
        assert "away_timeout" not in env.cfg.read_text()

    def test_missing_key_exits_one(self, env):
        res = env("settings", "unset", "ghost")
        assert res.exit_code == 1
        assert json.loads(res.output)["removed"] is False

    def test_dry_run_keeps_the_key(self, env):
        payload(env("settings", "unset", "away_timeout", "--dry-run"))
        assert "away_timeout" in env.cfg.read_text()

    def test_structural_block_is_refused(self, env):
        assert env("settings", "unset", "nodes").exit_code == 1


class TestSettingsToggles:
    def test_lists_locators(self, env):
        rows = payload(env("settings", "locators"))
        assert {r["name"] for r in rows} == {"nadaraya_watson", "nelder_mead"}

    def test_locator_status(self, env):
        out = payload(env("settings", "locator", "nelder_mead", "status"))
        assert out["enabled"] is False

    def test_locator_status_unknown_exits_one(self, env):
        assert env("settings", "locator", "ghost", "status").exit_code == 1

    def test_locator_off_writes(self, env):
        out = payload(env("settings", "locator", "nadaraya_watson", "off"))
        assert (out["before"], out["after"]) == (True, False)
        rows = {r["name"]: r for r in payload(env("settings", "locators"))}
        assert rows["nadaraya_watson"]["enabled"] is False

    def test_locator_on_writes(self, env):
        payload(env("settings", "locator", "nelder_mead", "on"))
        rows = {r["name"]: r for r in payload(env("settings", "locators"))}
        assert rows["nelder_mead"]["enabled"] is True

    def test_locator_dry_run(self, env):
        before = env.cfg.read_text()
        payload(env("settings", "locator", "nadaraya_watson", "off", "--dry-run"))
        assert env.cfg.read_text() == before

    def test_locator_unknown_exits_one(self, env):
        assert env("settings", "locator", "ghost", "off").exit_code == 1

    def test_lists_optimizers(self, env):
        assert [r["name"] for r in payload(env("settings", "optimizers"))] == ["absorption"]

    def test_optimizer_off(self, env):
        out = payload(env("settings", "optimizer", "absorption", "off"))
        assert out["after"] is False

    def test_optimizer_status(self, env):
        assert payload(env("settings", "optimizer", "absorption", "status"))["enabled"] is True

    def test_missing_section_exits_one(self, env):
        env.cfg.write_text("floors: []\n", encoding="utf-8")
        res = env("settings", "locators")
        assert res.exit_code == 1
        assert "no locators" in res.output

    def test_optimizers_accepts_the_optimization_spelling(self, env):
        env.cfg.write_text("optimization:\n  absorption:\n    enabled: true\n", encoding="utf-8")
        rows = payload(env("settings", "optimizers"))
        assert rows[0]["section"] == "optimization"


# ───────────────────────────────────────────── cross-command workflows


class TestWorkflows:
    def test_add_device_then_doctor_stays_clean(self, env):
        payload(env("devices", "add-to-config", "beacon-9", "--name", "Beacon"))
        assert payload(env("config", "doctor"))["ok"] is True

    def test_unnamed_device_is_warned_about_but_does_not_fail(self, env):
        payload(env("devices", "add-to-config", "beacon-9"))
        report = payload(env("config", "doctor"))
        assert report["ok"] is True
        assert "device_without_name" in {w["code"] for w in report["warnings"]}

    def test_naming_the_device_clears_the_warning(self, env):
        payload(env("devices", "add-to-config", "beacon-9"))
        payload(env("devices", "update-in-config", "beacon-9", "--name", "Beacon"))
        warned = {
            w.get("device")
            for w in payload(env("config", "doctor"))["warnings"]
            if w["code"] == "device_without_name"
        }
        # tile-1 in the fixture is still nameless; beacon-9 no longer is
        assert warned == {"tile-1"}

    def test_disabling_every_locator_is_caught_by_doctor(self, env):
        payload(env("settings", "locator", "nadaraya_watson", "off"))
        report = payload(env("config", "doctor"))
        assert "no_locator_enabled" in {w["code"] for w in report["warnings"]}

    def test_that_warning_fails_a_strict_doctor(self, env):
        payload(env("settings", "locator", "nadaraya_watson", "off"))
        assert env("config", "doctor", "--strict").exit_code == 1

    def test_re_enabling_one_locator_clears_it(self, env):
        payload(env("settings", "locator", "nadaraya_watson", "off"))
        payload(env("settings", "locator", "nelder_mead", "on"))
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "no_locator_enabled" not in codes

    def test_device_and_room_edits_compose_on_one_file(self, env):
        payload(env("devices", "add-to-config", "beacon-9", "--name", "Beacon"))
        payload(env("rooms", "rename", "Office", "Study"))
        payload(env("settings", "set", "timeout", "45"))
        text = env.cfg.read_text()
        assert "beacon-9" in text and "Study" in text and "timeout: 45" in text
        assert payload(env("config", "doctor"))["ok"] is True

    def test_a_full_retune_round_trips_through_the_file(self, env):
        payload(env("settings", "set", "mqtt.port", "8883"))
        payload(env("settings", "set", "mqtt.ssl", "true"))
        payload(env("settings", "unset", "away_timeout"))
        shown = payload(env("settings", "show", "--section", "mqtt"))["mqtt"]
        assert shown["port"] == 8883
        assert shown["ssl"] is True
        assert "away_timeout" not in payload(env("settings", "show"))
