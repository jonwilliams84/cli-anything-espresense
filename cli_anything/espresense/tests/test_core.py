"""Unit tests for cli-anything-espresense core modules.

These exercise the YAML edit logic against synthetic config docs — no
network, no kubectl, no MQTT broker required.
"""

from __future__ import annotations

import pytest

from cli_anything.espresense.core import nodes as nodes_core
from cli_anything.espresense.core import rooms as rooms_core
from cli_anything.espresense.utils import yaml_io


SAMPLE = """\
floors:
  - id: ground
    name: Ground Floor
    rooms:
      - name: Kitchen
        points: [[0,0],[1,0],[1,1],[0,1]]
      - name: Hall
        points: [[1,0],[2,0],[2,1],[1,1]]
  - id: first
    name: First Floor
    rooms:
      - name: Spare Room
        points: [[0,0],[1,0],[1,1],[0,1]]
      - name: Noah Bedroom
        points: [[1,0],[2,0],[2,1],[1,1]]
      - name: Sophie Bedroom
        points: [[2,0],[3,0],[3,1],[2,1]]
      - name: Master Bedroom
        points: [[3,0],[4,0],[4,1],[3,1]]

nodes:
  - name: kitchen
    point: [0.5, 0.5, 1.0]
    floors: ["ground"]
    room: Kitchen
  - name: noah-bedroom
    point: [0.5, 0.5, 1.0]
    floors: ["first"]
    room: "Sophie Bedroom "
  - name: sophie-bedroom
    point: [1.5, 0.5, 1.0]
    floors: ["first"]
    room: "Spare Room "
  - name: spare-room
    point: [2.5, 0.5, 1.0]
    floors: ["first"]
    room: Noah Bedroom
  - name: bedroom
    point: [3.5, 0.5, 1.0]
    floors: ["first"]
    room: "Master Bedroom "
"""


@pytest.fixture
def parsed():
    return yaml_io.load(SAMPLE)


# ── rooms.list_rooms ────────────────────────────────────────────────────────

class TestListRooms:
    def test_all_floors(self, parsed):
        rows = rooms_core.list_rooms(parsed)
        assert len(rows) == 6
        names = [r["room_name"] for r in rows]
        assert "Kitchen" in names
        assert "Sophie Bedroom" in names

    def test_floor_filter(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        assert len(rows) == 4
        assert all(r["floor_id"] == "first" for r in rows)

    def test_node_assignment_strips_whitespace(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        by_name = {r["room_name"]: r for r in rows}
        # noah-bedroom node's `room: Sophie Bedroom ` (trailing space) should
        # still join to the Sophie Bedroom polygon thanks to strip()
        assert "noah-bedroom" in by_name["Sophie Bedroom"]["node_names"]


# ── rooms.rename ────────────────────────────────────────────────────────────

class TestRename:
    def test_simple_rename(self, parsed):
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        assert summary["rooms_renamed"] == 1
        assert summary["nodes_repointed"] == 1  # kitchen node
        assert parsed["floors"][0]["rooms"][0]["name"] == "Cook Room"
        # node's room ref updated too
        kitchen_node = next(n for n in parsed["nodes"] if n["name"] == "kitchen")
        assert kitchen_node["room"] == "Cook Room"

    def test_rename_strips_node_whitespace_globally(self, parsed):
        summary = rooms_core.rename(parsed, "Sophie Bedroom", "Sophie Bedroom NEW")
        # `noah-bedroom` had room "Sophie Bedroom " with trailing space; after
        # strip and rename it should now point to "Sophie Bedroom NEW".
        noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        assert noah["room"] == "Sophie Bedroom NEW"
        # And `bedroom` node had room "Master Bedroom " — whitespace stripped
        # but value not renamed (since we renamed Sophie Bedroom only).
        bedroom = next(n for n in parsed["nodes"] if n["name"] == "bedroom")
        assert bedroom["room"] == "Master Bedroom"
        assert summary["whitespace_fixes"] >= 2

    def test_rename_noop(self, parsed):
        summary = rooms_core.rename(parsed, "Spare Room", "Spare Room")
        assert summary["rooms_renamed"] == 0
        assert summary["nodes_repointed"] == 0


# ── rooms.rotate ────────────────────────────────────────────────────────────

class TestRotate:
    def test_three_way_cycle(self, parsed):
        """The actual real-world case: A→B→C→A rotation should leave each
        physical polygon labeled with the post-rotation room name."""
        result = rooms_core.rotate(parsed, {
            "Spare Room": "Noah Bedroom",
            "Noah Bedroom": "Sophie Bedroom",
            "Sophie Bedroom": "Spare Room",
        })
        # All three should have rotated
        names = [r["name"] for r in parsed["floors"][1]["rooms"]]
        assert sorted(names) == sorted(
            ["Noah Bedroom", "Sophie Bedroom", "Spare Room", "Master Bedroom"]
        )
        # Floor index 0 -> originally "Spare Room", now "Noah Bedroom"
        assert parsed["floors"][1]["rooms"][0]["name"] == "Noah Bedroom"
        assert parsed["floors"][1]["rooms"][1]["name"] == "Sophie Bedroom"
        assert parsed["floors"][1]["rooms"][2]["name"] == "Spare Room"
        # Node room: references should follow the rotation too
        n_noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        n_sophie = next(n for n in parsed["nodes"] if n["name"] == "sophie-bedroom")
        n_spare = next(n for n in parsed["nodes"] if n["name"] == "spare-room")
        # noah-bedroom's room was "Sophie Bedroom" (with trailing space, stripped),
        # which rotated -> "Spare Room"
        assert n_noah["room"] == "Spare Room"
        # sophie-bedroom's room was "Spare Room" -> rotated to "Noah Bedroom"
        assert n_sophie["room"] == "Noah Bedroom"
        # spare-room's room was "Noah Bedroom" -> rotated to "Sophie Bedroom"
        assert n_spare["room"] == "Sophie Bedroom"
        # Master Bedroom node should be untouched
        n_master = next(n for n in parsed["nodes"] if n["name"] == "bedroom")
        assert n_master["room"] == "Master Bedroom"

    def test_rotate_rejects_duplicate_new(self, parsed):
        # dict literals can't have duplicate keys, so only `new` collisions
        # are reachable from CLI parsing. The validator should still catch it.
        with pytest.raises(ValueError, match="duplicate"):
            rooms_core.rotate(parsed, {"Kitchen": "X", "Hall": "X"})


# ── rooms.repoint_node ──────────────────────────────────────────────────────

class TestRepointNode:
    def test_found(self, parsed):
        out = rooms_core.repoint_node(parsed, "noah-bedroom", "Noah Bedroom")
        assert out["found"] is True
        assert out["after"] == "Noah Bedroom"
        n = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        assert n["room"] == "Noah Bedroom"

    def test_missing(self, parsed):
        out = rooms_core.repoint_node(parsed, "ghost-node", "Anywhere")
        assert out["found"] is False


# ── nodes module ────────────────────────────────────────────────────────────

class TestNodesCore:
    def test_list_config_nodes_strips_whitespace(self, parsed):
        rows = nodes_core.list_config_nodes(parsed)
        by_name = {r["name"]: r for r in rows}
        assert by_name["noah-bedroom"]["room"] == "Sophie Bedroom"
        assert by_name["noah-bedroom"]["room_raw"] == "Sophie Bedroom "

    def test_rename_in_config(self, parsed):
        out = nodes_core.rename_in_config(parsed, "spare-room", "noah-bedroom-new")
        assert out["found"] is True
        names = [n["name"] for n in parsed["nodes"]]
        assert "spare-room" not in names
        assert "noah-bedroom-new" in names

    def test_set_point(self, parsed):
        out = nodes_core.set_point(parsed, "kitchen", [9.0, 8.0, 7.0])
        assert out["found"] is True
        n = next(n for n in parsed["nodes"] if n["name"] == "kitchen")
        assert list(n["point"]) == [9.0, 8.0, 7.0]

    def test_remove(self, parsed):
        assert nodes_core.remove(parsed, "kitchen") is True
        names = [n["name"] for n in parsed["nodes"]]
        assert "kitchen" not in names
        assert nodes_core.remove(parsed, "ghost") is False


# ── yaml_io round-trip ──────────────────────────────────────────────────────

class TestYamlIO:
    def test_round_trip_preserves_structure(self):
        parsed = yaml_io.load(SAMPLE)
        text = yaml_io.dumps(parsed)
        reparsed = yaml_io.load(text)
        # node count, room count, names all preserved
        assert len(reparsed["nodes"]) == len(parsed["nodes"])
        assert sum(len(f["rooms"]) for f in reparsed["floors"]) == 6

    def test_edit_then_round_trip(self):
        parsed = yaml_io.load(SAMPLE)
        rooms_core.rotate(parsed, {
            "Spare Room": "Noah Bedroom",
            "Noah Bedroom": "Sophie Bedroom",
            "Sophie Bedroom": "Spare Room",
        })
        text = yaml_io.dumps(parsed)
        assert "Noah Bedroom" in text
        assert "Sophie Bedroom" in text
        assert "Spare Room" in text
        # round-trip stable
        reparsed = yaml_io.load(text)
        first_rooms = [r["name"] for r in reparsed["floors"][1]["rooms"]]
        assert first_rooms[0] == "Noah Bedroom"
        assert first_rooms[1] == "Sophie Bedroom"
        assert first_rooms[2] == "Spare Room"
