"""Unit tests for cli-anything-espresense core modules.

These exercise the YAML edit logic against synthetic config docs — no
network, no kubectl, no MQTT broker required.
"""

from __future__ import annotations

import pytest

from unittest.mock import MagicMock, patch

from cli_anything.espresense.core import (
    global_settings as global_settings_core,
    mqtt as mqtt_core,
    nodes as nodes_core,
    settings as settings_core,
)
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
        if len(rows) != 6:
            pytest.fail("Expected 6 rooms across all floors")
        names = [r["room_name"] for r in rows]
        if "Kitchen" not in names:
            pytest.fail("Kitchen should appear in room list")
        if "Sophie Bedroom" not in names:
            pytest.fail("Sophie Bedroom should appear in room list")

    def test_floor_filter(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        if len(rows) != 4:
            pytest.fail(f"Expected 4 rows on first floor, got {len(rows)}")
        if not all(r["floor_id"] == "first" for r in rows):
            pytest.fail("Not all rows have floor_id first")

    def test_node_assignment_strips_whitespace(self, parsed):
        rows = rooms_core.list_rooms(parsed, floor_id="first")
        by_name = {r["room_name"]: r for r in rows}
        # noah-bedroom node's `room: Sophie Bedroom ` (trailing space) should
        # still join to the Sophie Bedroom polygon thanks to strip()
        if "noah-bedroom" not in by_name["Sophie Bedroom"]["node_names"]:
            pytest.fail("noah-bedroom not found in node_names for Sophie Bedroom")


# ── rooms.rename ────────────────────────────────────────────────────────────


class TestRename:
    def test_simple_rename(self, parsed):
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        # B101 fix: use pytest.fail instead of assert to prevent removal with -O
        if summary["rooms_renamed"] != 1:
            pytest.fail(f"Expected rooms_renamed == 1, got {summary['rooms_renamed']}")
        if summary["nodes_repointed"] != 1:
            pytest.fail(
                f"Expected nodes_repointed == 1, got {summary['nodes_repointed']}"
            )  # kitchen node
        if parsed["floors"][0]["rooms"][0]["name"] != "Cook Room":
            pytest.fail(
                f"Expected room name 'Cook Room', got {parsed['floors'][0]['rooms'][0]['name']}"
            )
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
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        # All three should have rotated
        names = [r["name"] for r in parsed["floors"][1]["rooms"]]
        if sorted(names) != sorted(
            ["Noah Bedroom", "Sophie Bedroom", "Spare Room", "Master Bedroom"]
        ):
            pytest.fail("Expected rotated room names after three-way cycle")
        # Floor index 0 -> originally "Spare Room", now "Noah Bedroom"
        if parsed["floors"][1]["rooms"][0]["name"] != "Noah Bedroom":
            pytest.fail(
                f"Expected rooms[0].name == 'Noah Bedroom', got {parsed['floors'][1]['rooms'][0]['name']}"
            )
        if parsed["floors"][1]["rooms"][1]["name"] != "Sophie Bedroom":
            pytest.fail(
                f"Expected rooms[1].name == 'Sophie Bedroom', got {parsed['floors'][1]['rooms'][1]['name']}"
            )
        if parsed["floors"][1]["rooms"][2]["name"] != "Spare Room":
            pytest.fail(
                f"Expected rooms[2].name == 'Spare Room', got {parsed['floors'][1]['rooms'][2]['name']}"
            )
        # Node room: references should follow the rotation too
        n_noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        n_sophie = next(n for n in parsed["nodes"] if n["name"] == "sophie-bedroom")
        n_spare = next(n for n in parsed["nodes"] if n["name"] == "spare-room")
        # noah-bedroom's room was "Sophie Bedroom" (with trailing space, stripped),
        # which rotated -> "Spare Room"
        if n_noah["room"] != "Spare Room":
            pytest.fail(f"Expected n_noah['room'] == 'Spare Room', got {n_noah['room']}")
        # sophie-bedroom's room was "Spare Room" -> rotated to "Noah Bedroom"
        if n_sophie["room"] != "Noah Bedroom":
            pytest.fail(f"Expected n_sophie['room'] == 'Noah Bedroom', got {n_sophie['room']}")
        # spare-room's room was "Noah Bedroom" -> rotated to "Sophie Bedroom"
        if n_spare["room"] != "Sophie Bedroom":
            pytest.fail(f"Expected n_spare['room'] == 'Sophie Bedroom', got {n_spare['room']}")
        # Master Bedroom node should be untouched
        n_master = next(n for n in parsed["nodes"] if n["name"] == "bedroom")
        if not n_master["room"] == "Master Bedroom":
            pytest.fail("Assertion failed")

    def test_rotate_rejects_duplicate_new(self, parsed):
        # dict literals can't have duplicate keys, so only `new` collisions
        # are reachable from CLI parsing. The validator should still catch it.
        with pytest.raises(ValueError, match="duplicate"):
            rooms_core.rotate(parsed, {"Kitchen": "X", "Hall": "X"})


# ── rooms.repoint_node ──────────────────────────────────────────────────────


class TestRepointNode:
    def test_found(self, parsed):
        out = rooms_core.repoint_node(parsed, "noah-bedroom", "Noah Bedroom")
        if out["found"] is not True:
            pytest.fail("Assertion failed")
        if not out["after"] == "Noah Bedroom":
            pytest.fail("Assertion failed")
        n = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        if not n["room"] == "Noah Bedroom":
            pytest.fail("Assertion failed")

    def test_missing(self, parsed):
        out = rooms_core.repoint_node(parsed, "ghost-node", "Anywhere")
        if out["found"] is not False:
            pytest.fail("Assertion failed")


# ── nodes module ────────────────────────────────────────────────────────────


class TestNodesCore:
    def test_list_config_nodes_strips_whitespace(self, parsed):
        rows = nodes_core.list_config_nodes(parsed)
        by_name = {r["name"]: r for r in rows}
        if not by_name["noah-bedroom"]["room"] == "Sophie Bedroom":
            pytest.fail("Assertion failed")
        if not by_name["noah-bedroom"]["room_raw"] == "Sophie Bedroom ":
            pytest.fail("Assertion failed")

    def test_rename_in_config(self, parsed):
        out = nodes_core.rename_in_config(parsed, "spare-room", "noah-bedroom-new")
        if out["found"] is not True:
            pytest.fail("Assertion failed")
        names = [n["name"] for n in parsed["nodes"]]
        if not "spare-room" not in names:
            pytest.fail("Assertion failed")
        if "noah-bedroom-new" not in names:
            pytest.fail("Assertion failed")

    def test_set_point(self, parsed):
        out = nodes_core.set_point(parsed, "kitchen", [9.0, 8.0, 7.0])
        if out["found"] is not True:
            pytest.fail("Assertion failed")
        n = next(n for n in parsed["nodes"] if n["name"] == "kitchen")
        if not list(n["point"]) == [9.0, 8.0, 7.0]:
            pytest.fail("Assertion failed")

    def test_remove(self, parsed):
        if nodes_core.remove(parsed, "kitchen") is not True:
            pytest.fail("Assertion failed")
        names = [n["name"] for n in parsed["nodes"]]
        if not "kitchen" not in names:
            pytest.fail("Assertion failed")
        if nodes_core.remove(parsed, "ghost") is not False:
            pytest.fail("Assertion failed")


# ── yaml_io round-trip ──────────────────────────────────────────────────────


class TestYamlIO:
    def test_round_trip_preserves_structure(self):
        parsed = yaml_io.load(SAMPLE)
        text = yaml_io.dumps(parsed)
        reparsed = yaml_io.load(text)
        # node count, room count, names all preserved
        if not len(reparsed["nodes"]) == len(parsed["nodes"]):
            pytest.fail("Assertion failed")
        if not sum(len(f["rooms"]) for f in reparsed["floors"]) == 6:
            pytest.fail("Assertion failed")

    def test_edit_then_round_trip(self):
        parsed = yaml_io.load(SAMPLE)
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        text = yaml_io.dumps(parsed)
        if "Noah Bedroom" not in text:
            pytest.fail("Assertion failed")
        if "Sophie Bedroom" not in text:
            pytest.fail("Assertion failed")
        if "Spare Room" not in text:
            pytest.fail("Assertion failed")
        # round-trip stable
        reparsed = yaml_io.load(text)
        first_rooms = [r["name"] for r in reparsed["floors"][1]["rooms"]]
        if not first_rooms[0] == "Noah Bedroom":
            pytest.fail("Assertion failed")
        if not first_rooms[1] == "Sophie Bedroom":
            pytest.fail("Assertion failed")
        if not first_rooms[2] == "Spare Room":
            pytest.fail("Assertion failed")


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
                f"Expected all rows to have floor_id 'first', but found mismatches: {mismatches}"
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
            pytest.fail(
                "Sophie Bedroom not found in room list — whitespace stripping may be broken"
            )
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
            pytest.fail(f"Expected rooms_renamed == {expected}, got {summary['rooms_renamed']}")

    def test_rename_returns_nodes_repointed_count(self, parsed):
        """Regression for former assert summary["nodes_repointed"] == 1 (line 95).

        Ensures rename returns the correct count of nodes repointed.
        """
        summary = rooms_core.rename(parsed, "Kitchen", "Cook Room")
        expected = 1  # kitchen node
        if summary["nodes_repointed"] != expected:
            pytest.fail(f"Expected nodes_repointed == {expected}, got {summary['nodes_repointed']}")

    def test_rename_updates_floor_room_name(self, parsed):
        """Regression for former assert parsed["floors"][0]["rooms"][0]["name"] == "Cook Room" (line 96).

        Ensures the actual room name in the floor data structure is updated.
        """
        rooms_core.rename(parsed, "Kitchen", "Cook Room")
        actual = parsed["floors"][0]["rooms"][0]["name"]
        expected = "Cook Room"
        if actual != expected:
            pytest.fail(f"Expected room name '{expected}', got '{actual}'")


# ── global_settings ─────────────────────────────────────────────────────────
# The companion's deployment-wide settings (GET/POST /api/settings), which
# live outside config.yaml and are therefore reachable by no other module.


class FakeCompanion:
    """Records get/post calls against /api/settings like the real client."""

    def __init__(self, state: dict):
        self.state = state
        self.posts: list[dict] = []

    def get(self, path: str, params=None):
        assert path == global_settings_core.SETTINGS_API_PATH
        return dict(self.state)

    def post(self, path: str, json=None, data=None):
        assert path == global_settings_core.SETTINGS_API_PATH
        self.posts.append(dict(json))
        self.state.update(json)
        return {"ok": True}


class TestGlobalSettingsFetch:
    def test_returns_full_mapping_redacted(self):
        client = FakeCompanion({"telemetry": True, "expiration": 300, "mqtt_token": "s3cr3t"})
        out = global_settings_core.fetch(client)
        assert out["source"] == "/api/settings"
        assert out["settings"]["telemetry"] is True
        assert out["settings"]["mqtt_token"] == settings_core.REDACTED

    def test_reveal_passes_values_through(self):
        client = FakeCompanion({"mqtt_token": "s3cr3t"})
        out = global_settings_core.fetch(client, reveal=True)
        assert out["settings"]["mqtt_token"] == "s3cr3t"

    def test_single_key_read(self):
        client = FakeCompanion({"expiration": 300})
        out = global_settings_core.fetch(client, key="expiration")
        assert out == {"key": "expiration", "found": True, "value": 300, "secret": False}

    def test_single_secret_key_is_masked(self):
        client = FakeCompanion({"broker_password": "hunter2"})
        out = global_settings_core.fetch(client, key="broker_password")
        assert out["secret"] is True
        assert out["value"] == settings_core.REDACTED

    def test_single_secret_key_revealed(self):
        client = FakeCompanion({"broker_password": "hunter2"})
        out = global_settings_core.fetch(client, key="broker_password", reveal=True)
        assert out["value"] == "hunter2"

    def test_unknown_key_raises(self):
        client = FakeCompanion({"expiration": 300})
        with pytest.raises(global_settings_core.GlobalSettingsError, match="no_such_key"):
            global_settings_core.fetch(client, key="no_such_key")

    def test_error_names_settings_keys(self):
        client = FakeCompanion({})
        with pytest.raises(global_settings_core.GlobalSettingsError, match="settings-keys"):
            global_settings_core.fetch(client, key="nope")


class TestGlobalSettingsUpdate:
    def test_declared_kind_is_applied_for_known_keys(self):
        """expiration is declared int, so '300' must post as the number 300."""
        client = FakeCompanion({"expiration": 600})
        out = global_settings_core.update(client, "expiration", "300")
        assert client.posts == [{"expiration": 300}]
        assert out["after"] == 300
        assert out["before"] == 600
        assert out["changed"] is True

    def test_bool_key_coerces(self):
        client = FakeCompanion({"telemetry": True})
        out = global_settings_core.update(client, "telemetry", "off")
        assert client.posts == [{"telemetry": False}]
        assert out["after"] is False

    def test_json_key_parses_objects(self):
        client = FakeCompanion({})
        global_settings_core.update(client, "gps", '{"lat":51.5,"lng":-0.1}')
        assert client.posts == [{"gps": {"lat": 51.5, "lng": -0.1}}]

    def test_unknown_key_falls_back_to_auto(self):
        client = FakeCompanion({})
        global_settings_core.update(client, "brand_new_knob", "42")
        assert client.posts == [{"brand_new_knob": 42}]

    def test_explicit_kind_overrides_declared(self):
        client = FakeCompanion({})
        # 'on' is a legal channel label — force it to stay a string.
        global_settings_core.update(client, "expiration", "on", kind="str")
        assert client.posts == [{"expiration": "on"}]

    def test_unchanged_value_reports_changed_false(self):
        client = FakeCompanion({"expiration": 300})
        out = global_settings_core.update(client, "expiration", "300")
        assert out["changed"] is False

    def test_secret_values_are_masked_in_summary(self):
        client = FakeCompanion({"api_key": "abc"})
        out = global_settings_core.update(client, "api_key", "xyz")
        assert out["secret"] is True
        assert out["before"] == settings_core.REDACTED
        assert out["after"] == settings_core.REDACTED

    def test_bad_value_raises(self):
        client = FakeCompanion({})
        with pytest.raises(global_settings_core.GlobalSettingsError, match="integer"):
            global_settings_core.update(client, "expiration", "soon")

    def test_empty_key_raises(self):
        client = FakeCompanion({})
        with pytest.raises(global_settings_core.GlobalSettingsError, match="non-empty"):
            global_settings_core.update(client, "", "1")

    def test_key_with_slash_raises(self):
        client = FakeCompanion({})
        with pytest.raises(global_settings_core.GlobalSettingsError, match="/"):
            global_settings_core.update(client, "a/b", "1")


class TestGlobalSettingsDescribe:
    def test_lists_known_keys_sorted(self):
        keys = [row["key"] for row in global_settings_core.describe()]
        assert keys == sorted(keys)
        assert {"telemetry", "expiration", "gps"} <= set(keys)

    def test_rows_carry_kind_and_description(self):
        row = next(r for r in global_settings_core.describe() if r["key"] == "gps")
        assert row["kind"] == "json"
        assert "GPS" in row["description"]


class TestMqttPublishGlobalSetting:
    """The broker-side twin — topic shape, payload stringification, guard."""

    def _publish(self, value, key="expiration", retain=True, prefix="espresense"):
        with patch("cli_anything.espresense.core.mqtt.mqtt") as mock_mqtt:
            fake = MagicMock()
            mock_mqtt.Client.return_value = fake
            out = mqtt_core.publish_global_setting(
                "broker.local", key, value, retain=retain, prefix=prefix
            )
        return out, fake, mock_mqtt

    def test_topic_and_payload(self):
        out, fake, _ = self._publish(300)
        assert out["topic"] == "espresense/settings/expiration/set"
        assert out["payload"] == "300"
        fake.publish.assert_called_once_with(out["topic"], "300", qos=0, retain=True)

    def test_json_value_passes_as_json_text(self):
        out, _, _ = self._publish({"lat": 51.5}, key="gps")
        assert out["payload"] == '{"lat": 51.5}'

    def test_bool_value(self):
        out, _, _ = self._publish(True, key="telemetry")
        assert out["payload"] == "true"

    def test_no_retain(self):
        _, fake, _ = self._publish("1", retain=False)
        assert fake.publish.call_args[1]["retain"] is False

    def test_custom_prefix(self):
        out, _, _ = self._publish("1", prefix="home")
        assert out["topic"] == "home/settings/expiration/set"

    def test_empty_key_rejected(self):
        with pytest.raises(mqtt_core.MqttError, match="non-empty"):
            mqtt_core.publish_global_setting("broker.local", "", "1")

    def test_key_with_slash_rejected(self):
        with pytest.raises(mqtt_core.MqttError, match="/"):
            mqtt_core.publish_global_setting("broker.local", "a/b", "1")

    def test_client_disconnected_even_on_publish_failure(self):
        with patch("cli_anything.espresense.core.mqtt.mqtt") as mock_mqtt:
            fake = MagicMock()
            fake.publish.return_value.wait_for_publish.side_effect = RuntimeError("boom")
            mock_mqtt.Client.return_value = fake
            with pytest.raises(RuntimeError, match="boom"):
                mqtt_core.publish_global_setting("broker.local", "expiration", "1")
            fake.disconnect.assert_called_once()
