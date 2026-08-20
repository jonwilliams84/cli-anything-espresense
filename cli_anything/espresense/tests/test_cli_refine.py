"""End-to-end CLI tests for the commands added by the refine pass.

The `--file` config source means the whole rooms/nodes/floors editing surface
can now be driven for real — no kubectl, no pod, no mocks — against a YAML on
disk. Those tests are genuine end-to-end runs; only the HTTP/MQTT commands
are mocked at the transport boundary.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli_anything.espresense import espresense_cli as cli_mod
from cli_anything.espresense.espresense_cli import cli

CONFIG_YAML = """\
# ESPresense companion config
floors:
  - id: gf
    name: Ground Floor
    bounds: [[0, 0, 0], [10, 10, 3]]
    rooms:
      - name: Office
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
      - name: Kitchen
        points: [[4, 0], [8, 0], [8, 3], [4, 3]]
  - id: ff
    name: First Floor
    rooms: []
nodes:
  - name: office-node
    room: Office
    point: [1.0, 2.0, 2.5]
  - name: kitchen-node
    room: Kitchen
    point: [5.0, 2.0, 2.5]
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A CliRunner plus an isolated profile and a real config.yaml on disk."""
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
        return CliRunner().invoke(cli, argv)

    run.cfg = cfg  # type: ignore[attr-defined]
    run.profile = profile  # type: ignore[attr-defined]
    return run


def payload(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ─────────────────────────────────────────────── config doctor


class TestConfigDoctor:
    def test_clean_config_exits_zero(self, env):
        res = env("config", "doctor", "--file", str(env.cfg))
        data = payload(res)
        assert data["ok"] is True
        assert data["errors"] == []
        assert data["counts"] == {
            "floors": 2,
            "rooms": 2,
            "nodes": 2,
            "errors": 0,
            "warnings": 0,
        }

    def test_reports_source(self, env):
        data = payload(env("config", "doctor", "--file", str(env.cfg)))
        assert data["source"].startswith("file://")

    def test_dangling_room_ref_exits_one(self, env):
        env.cfg.write_text(CONFIG_YAML.replace("room: Office", "room: Ghost"), encoding="utf-8")
        res = env("config", "doctor", "--file", str(env.cfg))
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["ok"] is False
        assert "dangling_room_ref" in {e["code"] for e in data["errors"]}

    def test_whitespace_drift_exits_one(self, env):
        env.cfg.write_text(CONFIG_YAML.replace("room: Office", 'room: "Office "'), encoding="utf-8")
        res = env("config", "doctor", "--file", str(env.cfg))
        assert res.exit_code == 1
        assert "room_ref_whitespace" in {e["code"] for e in json.loads(res.output)["errors"]}

    def test_warnings_alone_exit_zero(self, env):
        env.cfg.write_text(
            CONFIG_YAML.replace(
                "  - name: kitchen-node\n    room: Kitchen\n    point: [5.0, 2.0, 2.5]\n", ""
            ),
            encoding="utf-8",
        )
        res = env("config", "doctor", "--file", str(env.cfg))
        assert res.exit_code == 0
        assert json.loads(res.output)["warnings"]

    def test_strict_turns_warnings_into_failure(self, env):
        env.cfg.write_text(
            CONFIG_YAML.replace(
                "  - name: kitchen-node\n    room: Kitchen\n    point: [5.0, 2.0, 2.5]\n", ""
            ),
            encoding="utf-8",
        )
        assert env("config", "doctor", "--strict", "--file", str(env.cfg)).exit_code == 1

    def test_human_readable_output(self, env):
        res = env("config", "doctor", "--file", str(env.cfg), json_out=False)
        assert res.exit_code == 0
        assert "config is clean" in res.output
        assert "2 floor(s)" in res.output

    def test_human_readable_lists_errors(self, env):
        env.cfg.write_text(CONFIG_YAML.replace("room: Office", "room: Ghost"), encoding="utf-8")
        res = env("config", "doctor", "--file", str(env.cfg), json_out=False)
        assert res.exit_code == 1
        assert "ERROR" in res.output
        assert "dangling_room_ref" in res.output

    def test_doctor_never_writes(self, env):
        before = env.cfg.read_text(encoding="utf-8")
        env("config", "doctor", "--file", str(env.cfg))
        assert env.cfg.read_text(encoding="utf-8") == before

    def test_missing_file_is_reported(self, env, tmp_path):
        res = env("config", "doctor", "--file", str(tmp_path / "absent.yaml"))
        assert res.exit_code != 0


# ─────────────────────────────────────────────── floors


class TestFloors:
    def test_list(self, env):
        data = payload(env("floors", "list", "--file", str(env.cfg)))
        assert [f["id"] for f in data] == ["gf", "ff"]
        assert data[0]["room_count"] == 2
        assert data[0]["node_count"] == 2

    def test_show_defaults_to_first_floor(self, env):
        data = payload(env("floors", "show", "--file", str(env.cfg)))
        assert data["id"] == "gf"

    def test_show_by_id(self, env):
        data = payload(env("floors", "show", "ff", "--file", str(env.cfg)))
        assert data["name"] == "First Floor"

    def test_show_unknown_floor_errors(self, env):
        res = env("floors", "show", "basement", "--file", str(env.cfg))
        assert res.exit_code == 1
        assert "no floor" in res.output


# ─────────────────────────────────────────────── rooms add / delete


class TestRoomsAdd:
    def test_adds_and_persists(self, env):
        data = payload(
            env(
                "rooms",
                "add",
                "ff",
                "Attic",
                "--point",
                "0,0",
                "--point",
                "2,0",
                "--point",
                "2,2",
                "--file",
                str(env.cfg),
            )
        )
        assert data["added"] is True
        assert data["point_count"] == 3
        assert data["pushed"]["source"] == "file"
        assert "Attic" in env.cfg.read_text(encoding="utf-8")

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text(encoding="utf-8")
        data = payload(
            env(
                "rooms", "add", "ff", "Attic", "--point", "0,0", "--dry-run", "--file", str(env.cfg)
            )
        )
        assert data["dry_run"] is True
        assert "pushed" not in data
        assert env.cfg.read_text(encoding="utf-8") == before

    def test_duplicate_name_rejected(self, env):
        res = env("rooms", "add", "ff", "Office", "--point", "0,0", "--file", str(env.cfg))
        assert res.exit_code == 1
        assert "already exists" in res.output

    def test_unknown_floor_rejected(self, env):
        res = env("rooms", "add", "zz", "X", "--point", "0,0", "--file", str(env.cfg))
        assert res.exit_code == 1

    @pytest.mark.parametrize("bad", ["1", "1,2,3", "a,b"])
    def test_malformed_point_rejected(self, env, bad):
        res = env("rooms", "add", "ff", "X", "--point", bad, "--file", str(env.cfg))
        assert res.exit_code == 1

    def test_point_is_required(self, env):
        res = env("rooms", "add", "ff", "X", "--file", str(env.cfg))
        assert res.exit_code != 0

    def test_color_is_written(self, env):
        env(
            "rooms",
            "add",
            "ff",
            "Attic",
            "--point",
            "0,0",
            "--color",
            "#abcdef",
            "--file",
            str(env.cfg),
        )
        assert "#abcdef" in env.cfg.read_text(encoding="utf-8")

    def test_comments_survive_the_write(self, env):
        env("rooms", "add", "ff", "Attic", "--point", "0,0", "--file", str(env.cfg))
        assert "# ESPresense companion config" in env.cfg.read_text(encoding="utf-8")


class TestRoomsDelete:
    def test_refuses_while_nodes_reference_it(self, env):
        res = env("rooms", "delete", "Office", "--file", str(env.cfg))
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["orphaned_nodes"] == ["office-node"]
        assert "refused" in data
        assert "Office" in env.cfg.read_text(encoding="utf-8")

    def test_force_overrides_the_refusal(self, env):
        res = env("rooms", "delete", "Office", "--force", "--file", str(env.cfg))
        data = payload(res)
        assert data["deleted"] is True
        assert data["pushed"]["source"] == "file"

    def test_deletes_cleanly_once_nodes_are_repointed(self, env):
        payload(env("rooms", "repoint-node", "office-node", "Kitchen", "--file", str(env.cfg)))
        data = payload(env("rooms", "delete", "Office", "--file", str(env.cfg)))
        assert data["deleted"] is True
        assert data["orphaned_nodes"] == []
        assert payload(env("config", "doctor", "--file", str(env.cfg)))["ok"] is True

    def test_missing_room_is_a_noop(self, env):
        data = payload(env("rooms", "delete", "Nope", "--file", str(env.cfg)))
        assert data["deleted"] is False


# ─────────────────────────────────────────────── nodes add / remove-from-config


class TestNodesAdd:
    def test_adds_with_room_and_point(self, env):
        data = payload(
            env(
                "nodes",
                "add",
                "attic-node",
                "--room",
                "Kitchen",
                "--point",
                "1.5,2.5,3",
                "--file",
                str(env.cfg),
            )
        )
        assert data["added"] is True
        assert data["point"] == [1.5, 2.5, 3.0]
        listed = payload(env("nodes", "list", "--no-merge-live", "--file", str(env.cfg)))
        assert "attic-node" in [n["name"] for n in listed]

    def test_duplicate_rejected(self, env):
        res = env("nodes", "add", "office-node", "--file", str(env.cfg))
        assert res.exit_code == 1
        assert "already exists" in res.output

    @pytest.mark.parametrize("bad", ["1,2", "a,b,c", "1,2,3,4"])
    def test_malformed_point_rejected(self, env, bad):
        assert env("nodes", "add", "n", "--point", bad, "--file", str(env.cfg)).exit_code == 1

    def test_flags_are_written(self, env):
        env("nodes", "add", "n", "--disabled", "--mobile", "--floor", "gf", "--file", str(env.cfg))
        text = env.cfg.read_text(encoding="utf-8")
        assert "enabled: false" in text
        assert "stationary: false" in text

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text(encoding="utf-8")
        payload(env("nodes", "add", "n", "--dry-run", "--file", str(env.cfg)))
        assert env.cfg.read_text(encoding="utf-8") == before


class TestNodesRemoveFromConfig:
    def test_removes_and_persists(self, env):
        data = payload(env("nodes", "remove-from-config", "office-node", "--file", str(env.cfg)))
        assert data["removed"] is True
        assert "office-node" not in env.cfg.read_text(encoding="utf-8")

    def test_unknown_node_is_a_noop(self, env):
        data = payload(env("nodes", "remove-from-config", "ghost", "--file", str(env.cfg)))
        assert data["removed"] is False
        assert "pushed" not in data

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text(encoding="utf-8")
        data = payload(
            env("nodes", "remove-from-config", "office-node", "--dry-run", "--file", str(env.cfg))
        )
        assert data["dry_run"] is True
        assert env.cfg.read_text(encoding="utf-8") == before


# ─────────────────────────────────────────────── --file on the pre-existing commands


class TestFileSourceOnExistingCommands:
    """The commands that previously required kubectl now work offline."""

    def test_rooms_list(self, env):
        data = payload(env("rooms", "list", "--file", str(env.cfg)))
        assert {r["room_name"] for r in data} == {"Office", "Kitchen"}
        assert data[0]["node_count"] == 1

    def test_rooms_list_floor_filter(self, env):
        assert payload(env("rooms", "list", "--floor", "ff", "--file", str(env.cfg))) == []

    def test_rooms_rename(self, env):
        data = payload(env("rooms", "rename", "Office", "Study", "--file", str(env.cfg)))
        assert data["rooms_renamed"] == 1
        assert data["nodes_repointed"] == 1
        text = env.cfg.read_text(encoding="utf-8")
        assert "Study" in text and "Office" not in text

    def test_rooms_rename_dry_run(self, env):
        before = env.cfg.read_text(encoding="utf-8")
        payload(env("rooms", "rename", "Office", "Study", "--dry-run", "--file", str(env.cfg)))
        assert env.cfg.read_text(encoding="utf-8") == before

    def test_rooms_rotate_cycle(self, env):
        data = payload(
            env(
                "rooms",
                "rotate",
                "--map",
                "Office=Kitchen",
                "--map",
                "Kitchen=Office",
                "--file",
                str(env.cfg),
            )
        )
        assert data["renames"]["Office"]["new"] == "Kitchen"
        after = payload(env("nodes", "list", "--no-merge-live", "--file", str(env.cfg)))
        by_name = {n["name"]: n["room"] for n in after}
        assert by_name["office-node"] == "Kitchen"
        assert by_name["kitchen-node"] == "Office"
        # a swap must leave the config valid
        assert payload(env("config", "doctor", "--file", str(env.cfg)))["ok"] is True

    def test_rooms_repoint_node(self, env):
        data = payload(
            env("rooms", "repoint-node", "office-node", "Kitchen", "--file", str(env.cfg))
        )
        assert data["found"] is True
        assert data["before"] == "Office"

    def test_nodes_rename_in_config(self, env):
        data = payload(
            env("nodes", "rename-in-config", "office-node", "study-node", "--file", str(env.cfg))
        )
        assert data["found"] is True
        assert "study-node" in env.cfg.read_text(encoding="utf-8")

    def test_nodes_set_point(self, env):
        data = payload(
            env("nodes", "set-point", "office-node", "9", "8", "7", "--file", str(env.cfg))
        )
        assert data["after"] == [9.0, 8.0, 7.0]
        assert data["before"] == [1.0, 2.0, 2.5]

    def test_nodes_list_config_only(self, env):
        data = payload(env("nodes", "list", "--no-merge-live", "--file", str(env.cfg)))
        assert [n["name"] for n in data] == ["office-node", "kitchen-node"]

    def test_restart_flag_is_reported_as_skipped(self, env):
        data = payload(
            env("rooms", "rename", "Office", "Study", "--restart", "--file", str(env.cfg))
        )
        assert data["pushed"]["restarted"] is False
        assert "restart_skipped" in data["pushed"]

    def test_a_backup_is_left_behind(self, env, tmp_path):
        env("rooms", "rename", "Office", "Study", "--file", str(env.cfg))
        assert list(tmp_path.glob("config.yaml.*.bak"))

    def test_no_kubectl_is_ever_invoked(self, env):
        with patch("cli_anything.espresense.core.k8s_backend._run") as run:
            payload(env("rooms", "list", "--file", str(env.cfg)))
            payload(env("rooms", "rename", "Office", "Study", "--file", str(env.cfg)))
            payload(env("config", "doctor", "--file", str(env.cfg)))
        run.assert_not_called()


# ─────────────────────────────────────────────── companion additions


class TestCompanionAdditions:
    def test_locator(self, env):
        client = MagicMock()
        client.get.return_value = {"state": "converged"}
        with patch.object(cli_mod, "make_client", return_value=client):
            data = payload(env("companion", "locator"))
        assert data == {"state": "converged"}
        client.get.assert_called_once_with("/api/state/locator")

    def test_firmware_types(self, env):
        client = MagicMock()
        client.get.return_value = {"flavors": ["m5stickc"]}
        with patch.object(cli_mod, "make_client", return_value=client):
            data = payload(env("companion", "firmware-types"))
        assert data == {"flavors": ["m5stickc"]}
        client.get.assert_called_once_with("/api/firmware/types")

    def test_pod_resolved(self, env):
        with patch.object(cli_mod.k8s_backend, "pod_name", return_value="espresense-abc"):
            data = payload(env("companion", "pod"))
        assert data["pod"] == "espresense-abc"
        assert data["resolved"] is True

    def test_pod_unresolved(self, env):
        with patch.object(cli_mod.k8s_backend, "pod_name", return_value=""):
            data = payload(env("companion", "pod"))
        assert data["resolved"] is False
        assert data["pod"] is None


# ─────────────────────────────────────────────── node direct additions


class TestNodeDirectAdditions:
    @pytest.fixture()
    def node_client(self):
        client = MagicMock()
        with patch.object(cli_mod, "_node_client", return_value=client):
            yield client

    def test_reboot(self, env, node_client):
        node_client.reboot.return_value = True
        data = payload(env("node", "reboot", "10.0.0.5"))
        assert data == {"host": "10.0.0.5", "rebooted": True}

    def test_config_list(self, env, node_client):
        node_client.list_device_configs.return_value = [{"id": "apple:1", "name": "Watch"}]
        data = payload(env("node", "config-list", "10.0.0.5"))
        assert data[0]["id"] == "apple:1"

    def test_config_set(self, env, node_client):
        node_client.upsert_device_config.return_value = {"ok": True}
        data = payload(
            env(
                "node",
                "config-set",
                "10.0.0.5",
                "apple:1",
                "--name",
                "Watch",
                "--rssi-at-1m",
                "-59",
            )
        )
        assert data["device_id"] == "apple:1"
        assert node_client.upsert_device_config.call_args.kwargs["rssi_at_1m"] == -59
        assert node_client.upsert_device_config.call_args.kwargs["name"] == "Watch"

    def test_config_set_requires_a_field(self, env, node_client):
        res = env("node", "config-set", "10.0.0.5", "apple:1")
        assert res.exit_code == 1
        assert "nothing to set" in res.output
        node_client.upsert_device_config.assert_not_called()

    def test_config_delete(self, env, node_client):
        node_client.delete_device_config.return_value = True
        data = payload(env("node", "config-delete", "10.0.0.5", "apple:1"))
        assert data["deleted"] is True


# ─────────────────────────────────────────────── mqtt set-device


class TestMqttSetDevice:
    @pytest.fixture()
    def profile_with_broker(self, env):
        env.profile.write_text(json.dumps({"mqtt_host": "broker.local"}))
        return env

    def test_publishes_to_settings_topic(self, profile_with_broker):
        env = profile_with_broker
        with patch.object(cli_mod.mqtt_core, "publish_device_config", return_value={"rc": 0}) as p:
            data = payload(env("mqtt", "set-device", "apple:1", '{"name": "Watch"}'))
        assert data == {"rc": 0}
        assert p.call_args.kwargs["device_id"] == "apple:1"
        assert p.call_args.kwargs["config"] == {"name": "Watch"}
        assert p.call_args.kwargs["host"] == "broker.local"
        assert p.call_args.kwargs["retain"] is True

    def test_no_retain_flag(self, profile_with_broker):
        env = profile_with_broker
        with patch.object(cli_mod.mqtt_core, "publish_device_config", return_value={}) as p:
            env("mqtt", "set-device", "apple:1", "{}", "--no-retain")
        assert p.call_args.kwargs["retain"] is False

    def test_custom_prefix(self, profile_with_broker):
        env = profile_with_broker
        with patch.object(cli_mod.mqtt_core, "publish_device_config", return_value={}) as p:
            env("mqtt", "set-device", "apple:1", "{}", "--prefix", "home")
        assert p.call_args.kwargs["prefix"] == "home"

    def test_invalid_json_rejected(self, profile_with_broker):
        env = profile_with_broker
        res = env("mqtt", "set-device", "apple:1", "{not json}")
        assert res.exit_code == 1
        assert "not valid JSON" in res.output

    def test_bad_device_id_is_surfaced(self, profile_with_broker):
        env = profile_with_broker
        res = env("mqtt", "set-device", "has/slash", "{}")
        assert res.exit_code == 1

    def test_missing_broker_is_reported(self, env):
        res = env("mqtt", "set-device", "apple:1", "{}")
        assert res.exit_code == 1
        assert "no MQTT broker configured" in res.output


# ─────────────────────────────────────────────── workflows


class TestWorkflows:
    """New commands composed with the pre-existing ones."""

    def test_provision_a_new_room_end_to_end(self, env):
        """add floor room -> add node -> verify -> doctor clean."""
        payload(
            env(
                "rooms",
                "add",
                "ff",
                "Attic",
                "--point",
                "0,0",
                "--point",
                "3,0",
                "--point",
                "3,3",
                "--point",
                "0,3",
                "--file",
                str(env.cfg),
            )
        )
        payload(
            env(
                "nodes",
                "add",
                "attic-node",
                "--room",
                "Attic",
                "--point",
                "1,1,2",
                "--file",
                str(env.cfg),
            )
        )
        rooms_out = payload(env("rooms", "list", "--floor", "ff", "--file", str(env.cfg)))
        assert rooms_out[0]["room_name"] == "Attic"
        assert rooms_out[0]["node_names"] == ["attic-node"]
        floors_out = payload(env("floors", "list", "--file", str(env.cfg)))
        assert floors_out[1]["room_count"] == 1
        assert payload(env("config", "doctor", "--file", str(env.cfg)))["ok"] is True

    def test_doctor_catches_a_half_finished_migration_then_rename_fixes_it(self, env):
        """Delete --force orphans a node; doctor flags it; repoint clears it."""
        payload(env("rooms", "delete", "Office", "--force", "--file", str(env.cfg)))
        res = env("config", "doctor", "--file", str(env.cfg))
        assert res.exit_code == 1
        codes = {e["code"] for e in json.loads(res.output)["errors"]}
        assert "dangling_room_ref" in codes

        payload(env("rooms", "repoint-node", "office-node", "Kitchen", "--file", str(env.cfg)))
        assert env("config", "doctor", "--file", str(env.cfg)).exit_code == 0

    def test_retire_a_node_from_both_sides(self, env):
        """`nodes delete` (companion) + `nodes remove-from-config` (YAML)."""
        client = MagicMock()
        with patch.object(cli_mod, "make_client", return_value=client):
            res = CliRunner().invoke(
                cli,
                ["--config", str(env.profile), "--json", "nodes", "delete", "office-node", "--yes"],
            )
        assert res.exit_code == 0
        payload(env("nodes", "remove-from-config", "office-node", "--file", str(env.cfg)))
        remaining = payload(env("nodes", "list", "--no-merge-live", "--file", str(env.cfg)))
        assert [n["name"] for n in remaining] == ["kitchen-node"]

    def test_fetch_edit_doctor_push_round_trip(self, env, tmp_path):
        """The workflow the README documents, now completable offline."""
        local = tmp_path / "edited.yaml"
        local.write_text(env.cfg.read_text(encoding="utf-8"), encoding="utf-8")

        payload(env("rooms", "rename", "Office", "Study", "--file", str(local)))
        assert payload(env("config", "doctor", "--file", str(local)))["ok"] is True

        pushed = {}

        def fake_write(target, text, backup=True):
            pushed["text"] = text

        with (
            patch.object(cli_mod.k8s_backend, "write_config", side_effect=fake_write),
            patch.object(cli_mod.k8s_backend, "restart"),
        ):
            res = CliRunner().invoke(
                cli,
                ["--config", str(env.profile), "--json", "companion", "config-push", str(local)],
            )
        assert res.exit_code == 0, res.output
        assert "Study" in pushed["text"]

    def test_doctor_gates_a_bad_push(self, env):
        """A config that fails doctor is exactly the one you must not push."""
        env.cfg.write_text(CONFIG_YAML.replace("room: Kitchen", "room: Ghost"), encoding="utf-8")
        assert env("config", "doctor", "--file", str(env.cfg)).exit_code == 1

    def test_every_new_command_supports_json(self, env):
        """--json is the harness-wide contract; new commands must honour it."""
        for args in (
            ["floors", "list", "--file", str(env.cfg)],
            ["floors", "show", "--file", str(env.cfg)],
            ["config", "doctor", "--file", str(env.cfg)],
            ["nodes", "add", "tmp-node", "--dry-run", "--file", str(env.cfg)],
            ["rooms", "add", "ff", "Tmp", "--point", "0,0", "--dry-run", "--file", str(env.cfg)],
            ["rooms", "delete", "Nope", "--file", str(env.cfg)],
            ["nodes", "remove-from-config", "ghost", "--file", str(env.cfg)],
        ):
            res = env(*args)
            assert res.exit_code == 0, (args, res.output)
            json.loads(res.output)  # must parse


class TestHelpSurface:
    def test_new_groups_are_discoverable(self):
        res = CliRunner().invoke(cli, ["--help"])
        assert res.exit_code == 0
        assert "floors" in res.output

    @pytest.mark.parametrize(
        "path",
        [
            ["config", "doctor"],
            ["floors", "list"],
            ["floors", "show"],
            ["rooms", "add"],
            ["rooms", "delete"],
            ["nodes", "add"],
            ["nodes", "remove-from-config"],
            ["companion", "locator"],
            ["companion", "firmware-types"],
            ["companion", "pod"],
            ["node", "reboot"],
            ["node", "config-list"],
            ["node", "config-set"],
            ["node", "config-delete"],
            ["mqtt", "set-device"],
            ["floors", "add"],
            ["floors", "rename"],
            ["floors", "retag"],
            ["floors", "set-bounds"],
            ["floors", "fit-bounds"],
            ["floors", "delete"],
            ["rooms", "geometry"],
            ["rooms", "locate"],
            ["rooms", "overlaps"],
            ["rooms", "set-points"],
            ["rooms", "move"],
            ["rooms", "scale"],
            ["rooms", "set-color"],
            ["nodes", "place"],
            ["devices", "list-in-config"],
            ["devices", "show-in-config"],
            ["devices", "add-to-config"],
            ["devices", "update-in-config"],
            ["devices", "remove-from-config"],
            ["settings", "show"],
            ["settings", "get"],
            ["settings", "set"],
            ["settings", "unset"],
            ["settings", "locators"],
            ["settings", "locator"],
            ["settings", "optimizers"],
            ["settings", "optimizer"],
        ],
    )
    def test_every_new_command_has_help(self, path):
        res = CliRunner().invoke(cli, [*path, "--help"])
        assert res.exit_code == 0
        assert res.output.strip()

    @pytest.mark.parametrize(
        "path",
        [
            ["rooms", "list"],
            ["rooms", "rename"],
            ["rooms", "rotate"],
            ["rooms", "repoint-node"],
            ["rooms", "add"],
            ["rooms", "delete"],
            ["nodes", "list"],
            ["nodes", "rename-in-config"],
            ["nodes", "set-point"],
            ["nodes", "add"],
            ["nodes", "remove-from-config"],
            ["floors", "list"],
            ["floors", "show"],
            ["config", "doctor"],
            ["floors", "add"],
            ["floors", "rename"],
            ["floors", "retag"],
            ["floors", "set-bounds"],
            ["floors", "fit-bounds"],
            ["floors", "delete"],
            ["rooms", "geometry"],
            ["rooms", "locate"],
            ["rooms", "overlaps"],
            ["rooms", "set-points"],
            ["rooms", "move"],
            ["rooms", "scale"],
            ["rooms", "set-color"],
            ["nodes", "place"],
            ["devices", "list-in-config"],
            ["devices", "show-in-config"],
            ["devices", "add-to-config"],
            ["devices", "update-in-config"],
            ["devices", "remove-from-config"],
            ["settings", "show"],
            ["settings", "get"],
            ["settings", "set"],
            ["settings", "unset"],
            ["settings", "locators"],
            ["settings", "locator"],
            ["settings", "optimizers"],
            ["settings", "optimizer"],
        ],
    )
    def test_every_config_editing_command_offers_file(self, path):
        """The --file escape hatch must be uniform, or it is a trap."""
        res = CliRunner().invoke(cli, [*path, "--help"])
        assert "--file" in res.output, path
