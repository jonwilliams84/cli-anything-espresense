"""Unit tests for the CRUD verbs added to rooms / nodes / config_yaml / mqtt."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cli_anything.espresense.core import config_yaml, mqtt, nodes, rooms, validate


def _cfg():
    return {
        "floors": [
            {
                "id": "gf",
                "name": "Ground",
                "bounds": [[0, 0, 0], [10, 10, 3]],
                "rooms": [{"name": "Office", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]}],
            },
            {"id": "ff", "name": "First", "rooms": []},
        ],
        "nodes": [{"name": "office-node", "room": "Office", "point": [1, 2, 2.5]}],
    }


class TestAddRoom:
    def test_adds_to_named_floor(self):
        cfg = _cfg()
        out = rooms.add_room(cfg, "ff", "Attic", [[0, 0], [2, 0], [2, 2]])
        assert out == {
            "added": True,
            "floor_id": "ff",
            "room_name": "Attic",
            "point_count": 3,
        }
        assert cfg["floors"][1]["rooms"][0]["name"] == "Attic"

    def test_creates_rooms_list_when_absent(self):
        cfg = _cfg()
        del cfg["floors"][1]["rooms"]
        rooms.add_room(cfg, "ff", "Attic", [[0, 0], [1, 0], [1, 1]])
        assert cfg["floors"][1]["rooms"][0]["name"] == "Attic"

    def test_color_is_optional(self):
        cfg = _cfg()
        rooms.add_room(cfg, "ff", "A", [[0, 0]], color="#abcdef")
        assert cfg["floors"][1]["rooms"][0]["color"] == "#abcdef"
        rooms.add_room(cfg, "ff", "B", [[0, 0]])
        assert "color" not in cfg["floors"][1]["rooms"][1]

    def test_rejects_duplicate_name_on_any_floor(self):
        cfg = _cfg()
        with pytest.raises(ValueError, match="already exists"):
            rooms.add_room(cfg, "ff", "Office", [[0, 0], [1, 0], [1, 1]])

    def test_rejects_unknown_floor(self):
        with pytest.raises(KeyError, match="no floor"):
            rooms.add_room(_cfg(), "basement", "X", [[0, 0]])

    def test_rejects_blank_name(self):
        with pytest.raises(ValueError, match="non-empty"):
            rooms.add_room(_cfg(), "ff", "  ", [[0, 0]])

    def test_name_is_stripped_so_it_cannot_be_born_dirty(self):
        cfg = _cfg()
        rooms.add_room(cfg, "ff", "  Attic  ", [[0, 0], [1, 0], [1, 1]])
        assert cfg["floors"][1]["rooms"][0]["name"] == "Attic"

    def test_added_room_passes_validation(self):
        cfg = _cfg()
        rooms.add_room(cfg, "ff", "Attic", [[0, 0], [2, 0], [2, 2]])
        nodes.add(cfg, "attic-node", room="Attic", point=[1, 1, 2])
        assert validate.check(cfg)["ok"] is True


class TestDeleteRoom:
    def test_removes_polygon(self):
        cfg = _cfg()
        out = rooms.delete_room(cfg, "Office")
        assert out["deleted"] is True
        assert out["rooms_removed"] == 1
        assert out["floor_id"] == "gf"
        assert cfg["floors"][0]["rooms"] == []

    def test_reports_orphaned_nodes_without_rewriting_them(self):
        cfg = _cfg()
        out = rooms.delete_room(cfg, "Office")
        assert out["orphaned_nodes"] == ["office-node"]
        # node is deliberately left pointing at the now-missing room
        assert cfg["nodes"][0]["room"] == "Office"

    def test_orphan_is_then_visible_to_the_validator(self):
        cfg = _cfg()
        rooms.delete_room(cfg, "Office")
        report = validate.check(cfg)
        assert validate.DANGLING_ROOM_REF in {f["code"] for f in report["errors"]}

    def test_missing_room_is_a_no_op(self):
        cfg = _cfg()
        out = rooms.delete_room(cfg, "Nope")
        assert out["deleted"] is False
        assert out["rooms_removed"] == 0
        assert len(cfg["floors"][0]["rooms"]) == 1

    def test_removes_every_duplicate(self):
        cfg = _cfg()
        cfg["floors"][1]["rooms"] = [{"name": "Office", "points": []}]
        out = rooms.delete_room(cfg, "Office")
        assert out["rooms_removed"] == 2


class TestAddNode:
    def test_minimal_add(self):
        cfg = _cfg()
        out = nodes.add(cfg, "new-node")
        assert out["added"] is True
        assert cfg["nodes"][-1] == {"name": "new-node"}

    def test_full_add(self):
        cfg = _cfg()
        nodes.add(cfg, "n", room="Office", point=[1, 2, 3], floors=["gf"])
        assert cfg["nodes"][-1] == {
            "name": "n",
            "room": "Office",
            "point": [1, 2, 3],
            "floors": ["gf"],
        }

    def test_rejects_duplicate(self):
        with pytest.raises(ValueError, match="already exists"):
            nodes.add(_cfg(), "office-node")

    def test_rejects_blank_name(self):
        with pytest.raises(ValueError, match="non-empty"):
            nodes.add(_cfg(), "")

    def test_room_is_stripped(self):
        cfg = _cfg()
        nodes.add(cfg, "n", room="  Office  ")
        assert cfg["nodes"][-1]["room"] == "Office"

    def test_flags_only_written_when_non_default(self):
        cfg = _cfg()
        nodes.add(cfg, "a")
        assert "enabled" not in cfg["nodes"][-1]
        assert "stationary" not in cfg["nodes"][-1]
        nodes.add(cfg, "b", enabled=False, stationary=False)
        assert cfg["nodes"][-1]["enabled"] is False
        assert cfg["nodes"][-1]["stationary"] is False

    def test_creates_nodes_list_when_absent(self):
        cfg = {"floors": []}
        nodes.add(cfg, "first")
        assert cfg["nodes"][0]["name"] == "first"

    def test_add_then_remove_round_trips(self):
        cfg = _cfg()
        nodes.add(cfg, "temp", room="Office")
        assert nodes.remove(cfg, "temp") is True
        assert [n["name"] for n in cfg["nodes"]] == ["office-node"]


class TestListFloors:
    def test_summarises_each_floor(self):
        out = config_yaml.list_floors(_cfg())
        assert [f["id"] for f in out] == ["gf", "ff"]
        assert out[0]["room_count"] == 1
        assert out[0]["room_names"] == ["Office"]
        assert out[0]["bounds"] == [[0, 0, 0], [10, 10, 3]]

    def test_counts_nodes_via_room_membership(self):
        out = config_yaml.list_floors(_cfg())
        assert out[0]["node_count"] == 1
        assert out[1]["node_count"] == 0

    def test_counts_nodes_via_explicit_floors_field(self):
        cfg = _cfg()
        cfg["nodes"].append({"name": "roamer", "floors": ["ff"]})
        out = config_yaml.list_floors(cfg)
        assert out[1]["node_count"] == 1

    def test_empty_config(self):
        assert config_yaml.list_floors({}) == []

    def test_interops_with_find_floor(self):
        cfg = _cfg()
        for row in config_yaml.list_floors(cfg):
            assert config_yaml.find_floor(cfg, row["id"])["id"] == row["id"]


class TestPublishDeviceConfig:
    def test_builds_the_settings_topic(self):
        with patch.object(mqtt, "publish_raw", return_value={"rc": 0}) as pr:
            mqtt.publish_device_config("broker", "apple:1005:9-12", {"name": "Watch"})
        assert pr.call_args.args[1] == "espresense/settings/apple:1005:9-12/config"

    def test_honours_custom_prefix(self):
        with patch.object(mqtt, "publish_raw", return_value={}) as pr:
            mqtt.publish_device_config("b", "dev", {"a": 1}, prefix="home")
        assert pr.call_args.args[1] == "home/settings/dev/config"

    def test_retains_by_default(self):
        with patch.object(mqtt, "publish_raw", return_value={}) as pr:
            mqtt.publish_device_config("b", "dev", {"a": 1})
        assert pr.call_args.kwargs["retain"] is True

    def test_passes_credentials_through(self):
        with patch.object(mqtt, "publish_raw", return_value={}) as pr:
            mqtt.publish_device_config("b", "dev", {"a": 1}, port=8883, username="u", password="p")
        assert pr.call_args.kwargs["port"] == 8883
        assert pr.call_args.kwargs["username"] == "u"

    @pytest.mark.parametrize("bad", ["", "has/slash"])
    def test_rejects_bad_device_id(self, bad):
        with pytest.raises(mqtt.MqttError):
            mqtt.publish_device_config("b", bad, {"a": 1})

    def test_rejects_non_mapping_config(self):
        with pytest.raises(mqtt.MqttError, match="mapping"):
            mqtt.publish_device_config("b", "dev", ["not", "a", "dict"])


class TestCoordinateStyle:
    """Added coordinates must render inline, like the hand-authored ones.

    Regression: a room added next to `points: [[0, 0], [4, 0]]` used to dump as
    a block ladder of `-   - 5.0`, which is valid YAML but visually alien in
    the file it was appended to.
    """

    def test_added_room_points_are_inline(self):
        from cli_anything.espresense.utils import yaml_io

        cfg = yaml_io.load(
            "floors:\n"
            "  - id: gf\n"
            "    rooms:\n"
            "      - name: Office\n"
            "        points: [[0, 0], [4, 0], [4, 3]]\n"
        )
        rooms.add_room(cfg, "gf", "Attic", [[5, 0], [9, 0], [9, 4]])
        out = yaml_io.dumps(cfg)
        assert "points: [[5, 0], [9, 0], [9, 4]]" in out
        assert "-   - 5" not in out

    def test_added_node_point_is_inline(self):
        from cli_anything.espresense.utils import yaml_io

        cfg = yaml_io.load("nodes:\n  - name: a\n    point: [1.0, 2.0, 3.0]\n")
        nodes.add(cfg, "b", point=[7.0, 2.0, 1.5])
        assert "point: [7.0, 2.0, 1.5]" in yaml_io.dumps(cfg)

    def test_set_point_rewrites_inline(self):
        from cli_anything.espresense.utils import yaml_io

        cfg = yaml_io.load("nodes:\n  - name: a\n    point: [1.0, 2.0, 3.0]\n")
        nodes.set_point(cfg, "a", [9.0, 8.0, 7.0])
        assert "point: [9.0, 8.0, 7.0]" in yaml_io.dumps(cfg)

    def test_flow_seq_is_still_an_ordinary_list(self):
        """Callers compare points with ==; the helper must not break that."""
        from cli_anything.espresense.utils import yaml_io

        seq = yaml_io.flow_seq([1, 2, 3])
        assert seq == [1, 2, 3]
        assert list(seq) == [1, 2, 3]
        assert len(seq) == 3

    def test_round_trip_preserves_existing_block_style(self):
        """The helper must not reformat sequences the user wrote as blocks."""
        from cli_anything.espresense.utils import yaml_io

        src = "nodes:\n  - name: a\n    point:\n      - 1.0\n      - 2.0\n      - 3.0\n"
        cfg = yaml_io.load(src)
        nodes.add(cfg, "b", point=[4.0, 5.0, 6.0])
        out = yaml_io.dumps(cfg)
        assert "      - 1.0" in out
        assert "point: [4.0, 5.0, 6.0]" in out
