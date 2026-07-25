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
        if len(rows) != 6: pytest.fail("Expected 6 rooms across all floors")
        names = [r["room_name"] for r in rows]
        if "Kitchen" not in names: pytest.fail("Kitchen should appear in room list")
        if "Sophie Bedroom" not in names: pytest.fail("Sophie Bedroom should appear in room list")

    def test_floor_filter(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        if len(rows) != 4: pytest.fail(f"Expected 4 rows on first floor, got {len(rows)}")
        if not all(r['floor_id'] == 'first' for r in rows): pytest.fail('Not all rows have floor_id first')

    def test_node_assignment_strips_whitespace(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        by_name = {r["room_name"]: r for r in rows}
        # noah-bedroom node's `room: Sophie Bedroom ` (trailing space) should
        # still join to the Sophie Bedroom polygon thanks to strip()
        if "noah-bedroom" not in by_name["Sophie Bedroom"]["node_names"]: pytest.fail("noah-bedroom not found in node_names for Sophie Bedroom")


# ── rooms.rename ────────────────────────────────────────────────────────────

class TestRename:
    def test_simple_rename(self, parsed):
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        # B101 fix: use pytest.fail instead of assert to prevent removal with -O
        if summary["rooms_renamed"] != 1:
            pytest.fail(f"Expected rooms_renamed == 1, got {summary['rooms_renamed']}")
        if summary["nodes_repointed"] != 1:
            pytest.fail(f"Expected nodes_repointed == 1, got {summary['nodes_repointed']}")  # kitchen node
        if parsed["floors"][0]["rooms"][0]["name"] != "Cook Room":
            pytest.fail(f"Expected room name 'Cook Room', got {parsed['floors'][0]['rooms'][0]['name']}")
        # node's room ref updated too
        kitchen_node = next(n for n in parsed["nodes"] if n["name"] == "kitchen")
        if kitchen_node["room"] != "Cook Room":
            pytest.fail(f"Expected kitchen node room == 'Cook Room', got {kitchen_node['room']}")

    def test_rename_strips_node_whitespace_globally(self, parsed):
        summary = rooms_core.rename(parsed, "Sophie Bedroom", "Sophie Bedroom NEW")
        # `noah-bedroom` had room "Sophie Bedroom " with trailing space; after
        # strip and rename it should now point to "Sophie Bedroom NEW".
        noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        if noah["room"] != "Sophie Bedroom NEW":
            pytest.fail(f"Expected noah node room == 'Sophie Bedroom NEW', got {noah['room']}")
        # And `bedroom` node had room "Master Bedroom " — whitespace stripped
        # but value not renamed (since we renamed Sophie Bedroom only).
        bedroom = next(n for n in parsed["nodes"] if n["name"] == "bedroom")
        if bedroom["room"] != "Master Bedroom":
            pytest.fail(f"Expected bedroom node room == 'Master Bedroom', got {bedroom['room']}")
        if summary["whitespace_fixes"] < 2:
            pytest.fail(f"Expected whitespace_fixes >= 2, got {summary['whitespace_fixes']}")

    def test_rename_noop(self, parsed):
        summary = rooms_core.rename(parsed, "Spare Room", "Spare Room")
        if summary["rooms_renamed"] != 0:
            pytest.fail("Expected rooms_renamed == 0 for no-op rename")
        if summary["nodes_repointed"] != 0:
            pytest.fail("Expected nodes_repointed == 0 for no-op rename")


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
        if sorted(names) != sorted(["Noah Bedroom", "Sophie Bedroom", "Spare Room", "Master Bedroom"]):
            pytest.fail("Expected rotated room names after three-way cycle")
        # Floor index 0 -> originally "Spare Room", now "Noah Bedroom"
        if parsed["floors"][1]["rooms"][0]["name"] != "Noah Bedroom":
            pytest.fail(f"Expected rooms[0].name == 'Noah Bedroom', got {parsed['floors'][1]['rooms'][0]['name']}")
        if parsed["floors"][1]["rooms"][1]["name"] != "Sophie Bedroom":
            pytest.fail(f"Expected rooms[1].name == 'Sophie Bedroom', got {parsed['floors'][1]['rooms'][1]['name']}")
        if parsed["floors"][1]["rooms"][2]["name"] != "Spare Room":
            pytest.fail(f"Expected rooms[2].name == 'Spare Room', got {parsed['floors'][1]['rooms'][2]['name']}")
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




# ── Regression: B101 assert_used fixes ───────────────────────────────────────
# The following tests replace assert statements that were flagged by bandit
# B101 (assert_used). Each guarded assertion is now expressed as a pytest.fail()
# call so the check is never optimized away. These tests verify the SAME
# conditions but in a dedicated regression class.

class TestB101Regression:
    """Regression tests: previously used bare assert, now use pytest.fail().
    These ensure the checks cannot be silently stripped by -O compilation.
    """

    def test_list_rooms_floor_filter_returns_correct_count(self, parsed):
        """Regression for former assert len(rows) == 4 (line 78)."""
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        expected = 4
        if len(rows) != expected:
            pytest.fail(f"Expected {expected} rooms on floor 'first', got {len(rows)}")

    def test_list_rooms_floor_filter_all_match_floor(self, parsed):
        """Regression for former assert all(... for r in rows) (line 79)."""
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        mismatches = [r for r in rows if r["floor_id"] != "first"]
        if mismatches:
            pytest.fail(
                f"Expected all rows to have floor_id 'first', "
                f"but found mismatches: {mismatches}"
            )

    def test_node_assignment_whitespace_stripping(self, parsed):
        """Regression for former assert 'noah-bedroom' in node_names (line 86).

        The noah-bedroom node has room: "Sophie Bedroom " (trailing space).
        list_rooms must strip whitespace so the node joins the Sophie Bedroom
        polygon correctly.
        """
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        by_name = {r["room_name"]: r for r in rows}
        if "Sophie Bedroom" not in by_name:
            pytest.fail("Sophie Bedroom not found in room list — whitespace stripping may be broken")
        if "noah-bedroom" not in by_name["Sophie Bedroom"]["node_names"]:
            pytest.fail(
                "noah-bedroom node not assigned to Sophie Bedroom — "
                "whitespace stripping of node room field may be broken"
            )

    def test_rename_returns_rooms_renamed_count(self, parsed):
        """Regression for former assert summary["rooms_renamed"] == 1 (line 94).

        Ensures rename returns the correct count of rooms renamed.
        """
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        expected = 1
        if summary["rooms_renamed"] != expected:
            pytest.fail(
                f"Expected rooms_renamed == {expected}, got {summary['rooms_renamed']}"
            )

    def test_rename_returns_nodes_repointed_count(self, parsed):
        """Regression for former assert summary["nodes_repointed"] == 1 (line 95).

        Ensures rename returns the correct count of nodes repointed.
        """
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        expected = 1  # kitchen node
        if summary["nodes_repointed"] != expected:
            pytest.fail(
                f"Expected nodes_repointed == {expected}, got {summary['nodes_repointed']}"
            )

    def test_rename_updates_floor_room_name(self, parsed):
        """Regression for former assert parsed["floors"][0]["rooms"][0]["name"] == "Cook Room" (line 96).

        Ensures the actual room name in the floor data structure is updated.
        """
        rooms_core.rename(parsed, "Kitchen", "Cook Room")
        actual = parsed["floors"][0]["rooms"][0]["name"]
        expected = "Cook Room"
        if actual != expected:
            pytest.fail(f"Expected room name '{expected}', got '{actual}'")

