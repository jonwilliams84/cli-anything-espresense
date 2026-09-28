"""End-to-end tests for the live presence-query commands (v0.3.0 refine pass).

Runs the real Click CLI (CliRunner) with mocked transports — no companion, no
broker — asserting that the new commands parse `--json`, render human tables,
fail cleanly, and compose with the existing `history` / `mqtt watch` commands.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli_anything.espresense.espresense_cli import cli
from cli_anything.espresense.utils import yaml_io

# ── shared helpers ───────────────────────────────────────────────────────────


def _cfg(tmp_path, body="{}"):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(body)
    return cfg_path


DIST_RECORDS = [
    {"topic": "espresense/rooms/kitchen/devices/d1", "payload": "3.0", "ts": 101.0},
    {"topic": "espresense/rooms/hall/devices/d1", "payload": "5.0", "ts": 102.0},
    {"topic": "espresense/rooms/hall/devices/d1", "payload": "4.5", "ts": 103.0},
    {"topic": "espresense/rooms/kitchen/devices/d2", "payload": "2.0", "ts": 104.0},
]

STATUS_RECORDS = [
    {"topic": "espresense/rooms/kitchen/status", "payload": "online", "ts": 1.0},
    {"topic": "espresense/rooms/hall/status", "payload": "offline", "ts": 1.0},
]

TELEM_RECORDS = [
    {
        "topic": "espresense/rooms/kitchen/telemetry",
        "payload": '{"uptime": 3600, "freeMem": 143000, "rssi": -62, "ip": "10.0.0.5", "ver": "1.0"}',
        "ts": 10.0,
    },
    {
        "topic": "espresense/rooms/kitchen/telemetry",
        "payload": '{"uptime": 3700, "freeMem": 139000, "rssi": -64, "ip": "10.0.0.5", "ver": "1.0"}',
        "ts": 11.0,
    },
    {
        "topic": "espresense/rooms/hall/telemetry",
        "payload": '{"uptime": 120, "freeMem": 220000, "rssi": -55, "ip": "10.0.0.6", "ver": "1.0"}',
        "ts": 12.0,
    },
]


# ── devices whereis ──────────────────────────────────────────────────────────


class TestDevicesWhereisE2E:
    def test_json_last_known_position(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = {
            "history": [
                {"x": 1.0, "y": 1.0, "z": 1.0, "roomName": "Kitchen", "unixTs": 1},
                {"x": 2.5, "y": 3.0, "z": 0.9, "roomName": "Office", "unixTs": 42},
            ]
        }
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "devices", "whereis", "d1"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out == {
            "device_id": "d1",
            "found": True,
            "room": "Office",
            "floor": None,
            "x": 2.5,
            "y": 3.0,
            "z": 0.9,
            "when": 42,
        }

    def test_never_seen_exits_1_but_still_emits_json(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = {"history": []}
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "devices", "whereis", "ghost"]
            )
        assert result.exit_code == 1
        assert json.loads(result.output) == {"device_id": "ghost", "found": False}

    def test_human_output_names_the_room(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = {
            "history": [{"x": 2.5, "y": 3.0, "roomName": "Office", "unixTs": 42}]
        }
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "devices", "whereis", "d1"])
        assert result.exit_code == 0
        assert "Office" in result.output

    def test_empty_device_id_aborts(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        result = CliRunner().invoke(cli, ["--config", str(cfg), "devices", "whereis", "  "])
        assert result.exit_code == 1
        assert "non-empty" in result.output

    def test_help(self):
        result = CliRunner().invoke(cli, ["devices", "whereis", "--help"])
        assert result.exit_code == 0
        assert "history" in result.output


# ── devices occupancy ────────────────────────────────────────────────────────


class TestDevicesOccupancyE2E:
    DEVICE_ROWS = [
        {"id": "d1", "name": "Phone", "room": "Office", "floor": "Ground"},
        {"id": "d2", "name": "Watch", "room": "Office", "floor": "Ground"},
        {"id": "d3", "name": "Tag", "room": None, "floor": "Ground"},
    ]

    def test_json_groups_devices_by_room(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = self.DEVICE_ROWS
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "devices", "occupancy"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["rooms"]["Office"] == [
            {"id": "d1", "name": "Phone"},
            {"id": "d2", "name": "Watch"},
        ]
        assert out["unplaced"] == [{"id": "d3", "name": "Tag"}]

    def test_floor_filter_is_passed_through(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = self.DEVICE_ROWS
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "devices", "occupancy", "--floor", "ground"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert set(out["rooms"]) == {"Office"}

    def test_human_output_lists_room_and_occupants(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = self.DEVICE_ROWS
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "devices", "occupancy"])
        assert result.exit_code == 0
        assert "Office" in result.output
        assert "Phone" in result.output

    def test_help(self):
        result = CliRunner().invoke(cli, ["devices", "occupancy", "--help"])
        assert result.exit_code == 0


# ── mqtt distances ───────────────────────────────────────────────────────────


class TestMqttDistancesE2E:
    def test_requires_broker(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "distances"])
        assert result.exit_code == 1
        assert "no MQTT broker" in result.output

    def test_json_snapshot(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=DIST_RECORDS,
        ) as mock_watch:
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "mqtt", "distances", "--duration", "3"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["topic_filter"] == "espresense/rooms/+/devices/+"
        assert out["duration"] == 3
        assert out["messages"] == 4
        assert out["devices"]["d1"]["hall"]["distance"] == 4.5
        assert out["nearest"]["d1"][0]["node"] == "kitchen"
        mock_watch.assert_called_once()

    def test_filters_reach_the_snapshot(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch("cli_anything.espresense.core.telemetry.mqtt_core.watch", return_value=[]):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(cfg),
                    "--json",
                    "mqtt",
                    "distances",
                    "--device",
                    "d1",
                    "--node",
                    "kitchen",
                    "--prefix",
                    "home",
                ],
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["topic_filter"] == "home/rooms/+/devices/+"

    def test_human_output_renders_distance_table(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch", return_value=DIST_RECORDS
        ):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "distances"])
        assert result.exit_code == 0
        assert "kitchen" in result.output
        assert "nearest" in result.output

    def test_help(self):
        result = CliRunner().invoke(cli, ["mqtt", "distances", "--help"])
        assert result.exit_code == 0


# ── mqtt node-status ─────────────────────────────────────────────────────────


class TestMqttNodeStatusE2E:
    def test_requires_broker(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "node-status"])
        assert result.exit_code == 1
        assert "no MQTT broker" in result.output

    def test_json_online_offline(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=STATUS_RECORDS,
        ) as mock_watch:
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "mqtt", "node-status"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["online"] == ["kitchen"]
        assert out["offline"] == ["hall"]
        assert mock_watch.call_args[0][1] == "espresense/rooms/+/status"

    def test_human_output_lists_both_sides(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch", return_value=STATUS_RECORDS
        ):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "node-status"])
        assert result.exit_code == 0
        assert "online:" in result.output
        assert "kitchen" in result.output
        assert "hall" in result.output

    def test_help(self):
        result = CliRunner().invoke(cli, ["mqtt", "node-status", "--help"])
        assert result.exit_code == 0


class TestMqttTelemetryE2E:
    def test_requires_broker(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "telemetry"])
        assert result.exit_code == 1
        assert "no MQTT broker" in result.output

    def test_json_snapshot(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=TELEM_RECORDS,
        ) as mock_watch:
            result = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "mqtt", "telemetry"])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["nodes_reporting"] == ["hall", "kitchen"]
        kitchen = out["nodes"]["kitchen"]
        assert kitchen["samples"] == 2
        assert kitchen["uptime"] == 3700  # max over the window
        assert kitchen["free_mem"] == 139000  # min over the window
        assert kitchen["latest"]["ip"] == "10.0.0.5"
        assert kitchen["latest"]["version"] == "1.0"  # `ver` normalised
        assert out["lowest_free_mem"] == {"node": "kitchen", "free_mem": 139000}
        assert out["topic_filter"] == "espresense/rooms/+/telemetry"
        assert mock_watch.call_args[0][1] == "espresense/rooms/+/telemetry"

    def test_node_filter_flows_to_snapshot(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=TELEM_RECORDS,
        ):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "mqtt", "telemetry", "--node", "hall"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["nodes_reporting"] == ["hall"]
        assert "kitchen" not in out["nodes"]

    def test_human_output_renders_health_table(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=TELEM_RECORDS,
        ):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "telemetry"])
        assert result.exit_code == 0
        assert "kitchen" in result.output
        assert "139000" in result.output  # the min free_mem, worst case
        assert "10.0.0.5" in result.output

    def test_human_output_when_nothing_heard(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch("cli_anything.espresense.core.telemetry.mqtt_core.watch", return_value=[]):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "telemetry"])
        assert result.exit_code == 0
        assert "no node telemetry" in result.output

    def test_help(self):
        result = CliRunner().invoke(cli, ["mqtt", "telemetry", "--help"])
        assert result.exit_code == 0
        assert "telemetry" in result.output


# ── workflows: the new commands compose with the existing ones ───────────────


class TestPresenceWorkflow:
    """distances is the aggregated view of exactly what `mqtt watch` streams."""

    def test_distances_agrees_with_a_raw_watch_of_the_same_topic(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.mqtt.watch", return_value=DIST_RECORDS
        ) as mock_watch:
            snapshot = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "mqtt", "distances", "--duration", "1"]
            )
            firehose = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(cfg),
                    "--json",
                    "mqtt",
                    "watch",
                    "espresense/rooms/+/devices/+",
                    "--duration",
                    "1",
                ],
            )
        assert snapshot.exit_code == 0 and firehose.exit_code == 0
        assert mock_watch.call_count == 2
        # both commands subscribe to the same topic filter (positional vs kwarg)
        snapshot_filter = mock_watch.call_args_list[0].args[1]
        watched_filter = (
            mock_watch.call_args_list[1].kwargs.get("topic_filter")
            or mock_watch.call_args_list[1].args[1]
        )
        assert watched_filter == snapshot_filter
        snap = json.loads(snapshot.output)
        watched = json.loads(firehose.output)
        # every row in the snapshot comes from a message the firehose saw
        snapshot_topics = {f"{r.get('topic')}" for r in watched}
        for dev, nodes in snap["devices"].items():
            for node in nodes:
                assert f"espresense/rooms/{node}/devices/{dev}" in snapshot_topics

    def test_whereis_then_occupancy_for_one_device(self, tmp_path, monkeypatch):
        """The device's last-known room must be a room occupancy reports as occupied."""
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)

        def fake_get(path, params=None):
            if path.startswith("/api/history/"):
                return {"history": [{"x": 1.0, "y": 1.0, "roomName": "Office", "unixTs": 5}]}
            if path.startswith("/api/state/devices"):
                return [
                    {
                        "id": "d1",
                        "name": "Phone",
                        "room": {"name": "Office"},
                        "floor": {"name": "G"},
                    },
                    {"id": "d9", "name": "Tag", "room": None},
                ]
            raise AssertionError(f"unexpected path {path}")

        client = MagicMock()
        client.get.side_effect = fake_get
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            where = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "devices", "whereis", "d1"]
            )
            occ = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "devices", "occupancy"])
        assert where.exit_code == 0 and occ.exit_code == 0
        room = json.loads(where.output)["room"]
        assert room in json.loads(occ.output)["rooms"]


# ── history trail ────────────────────────────────────────────────────────────


class TestHistoryTrailE2E:
    ROWS = [
        {"x": 1.0, "y": 1.0, "roomName": "Kitchen", "unixTs": 1},
        {"x": 1.2, "y": 1.1, "roomName": "Kitchen", "unixTs": 2},
        {"x": 2.5, "y": 3.0, "roomName": "Office", "unixTs": 7},
    ]

    def _client(self):
        client = MagicMock()
        client.get.return_value = {"history": self.ROWS}
        return client

    def test_json_movement_summary(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.espresense_cli.make_client",
            return_value=self._client(),
        ):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "history", "trail", "d1"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["device_id"] == "d1"
        assert out["points"] == 3
        assert out["first_seen"] == 1
        assert out["last_seen"] == 7
        assert out["rooms_visited"] == ["Kitchen", "Office"]
        assert out["segments"] == [
            {"room": "Kitchen", "points": 2, "first_seen": 1, "last_seen": 2},
            {"room": "Office", "points": 1, "first_seen": 7, "last_seen": 7},
        ]

    def test_human_output_is_summary_plus_segment_table(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.espresense_cli.make_client",
            return_value=self._client(),
        ):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "history", "trail", "d1"])
        assert result.exit_code == 0
        assert "Kitchen, Office" in result.output
        assert "Kitchen" in result.output and "Office" in result.output

    def test_limit_is_applied_before_folding(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.espresense_cli.make_client",
            return_value=self._client(),
        ):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "history", "trail", "d1", "--limit", "1"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["points"] == 1
        assert out["rooms_visited"] == ["Office"]

    def test_never_seen_still_emits_empty_summary(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = {"history": []}
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "history", "trail", "ghost"]
            )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["points"] == 0
        assert out["segments"] == []

    def test_help(self):
        result = CliRunner().invoke(cli, ["history", "trail", "--help"])
        assert result.exit_code == 0
        assert "Summarise where a device has been" in result.output


class TestHistoryWorkflow:
    """`history get` is the firehose; `history trail` is its readable fold —
    the two must agree on the same transport call."""

    def test_trail_segments_are_consistent_with_raw_rows(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = {
            "history": [
                {"roomName": "Kitchen", "unixTs": 1},
                {"roomName": "Office", "unixTs": 2},
                {"roomName": "Office", "unixTs": 3},
                {"roomName": "Kitchen", "unixTs": 4},
            ]
        }
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            raw = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "history", "get", "d1"])
            trail = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "history", "trail", "d1"]
            )
        assert raw.exit_code == 0 and trail.exit_code == 0
        rows = json.loads(raw.output)
        out = json.loads(trail.output)
        # both commands hit the same endpoint with the same device
        assert all(call.args[0] == "/api/history/d1" for call in client.get.call_args_list)
        assert out["points"] == len(rows)
        # folding the raw rows by hand must give the same segments
        segments = []
        for row in rows:
            if not segments or segments[-1]["room"] != row["roomName"]:
                segments.append(
                    {
                        "room": row["roomName"],
                        "points": 1,
                        "first_seen": row["unixTs"],
                        "last_seen": row["unixTs"],
                    }
                )
            else:
                segments[-1]["points"] += 1
                segments[-1]["last_seen"] = row["unixTs"]
        assert out["segments"] == segments


class TestNodeHealthWorkflow:
    """`mqtt telemetry` and `mqtt node-status` describe the same fleet.

    Both subscribe to the `<prefix>/rooms/<node>/…` topic family, so the set
    of nodes each command hears must agree — an operator cross-checking a
    flapping node should never get two different stories from the broker.
    """

    def test_telemetry_and_status_see_the_same_nodes(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=TELEM_RECORDS,
        ):
            telem = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "mqtt", "telemetry"])
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=[
                {"topic": "espresense/rooms/kitchen/status", "payload": "online", "ts": 1},
                {"topic": "espresense/rooms/hall/status", "payload": "online", "ts": 1},
            ],
        ):
            status = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "mqtt", "node-status"]
            )
        assert telem.exit_code == 0 and status.exit_code == 0
        telem_nodes = set(json.loads(telem.output)["nodes_reporting"])
        status_nodes = set(json.loads(status.output)["online"])
        assert telem_nodes == status_nodes == {"kitchen", "hall"}

    def test_telemetry_rows_agree_with_the_json_snapshot(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, json.dumps({"mqtt_host": "broker.local"}))
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.core.telemetry.mqtt_core.watch",
            return_value=TELEM_RECORDS,
        ):
            as_json = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "mqtt", "telemetry"])
            as_table = CliRunner().invoke(cli, ["--config", str(cfg), "mqtt", "telemetry"])
        assert as_json.exit_code == 0 and as_table.exit_code == 0
        out = json.loads(as_json.output)
        # every node in the JSON snapshot appears in the human table, and the
        # worst-case free_mem shown is the min the aggregation computed
        for node, entry in out["nodes"].items():
            assert node in as_table.output
            if entry.get("free_mem") is not None:
                assert str(entry["free_mem"]) in as_table.output


class TestHistoryHeatmapE2E:
    """`history heatmap` — room usage across many devices (v0.8.0 refine)."""

    ROWS = {
        "d1": [
            {"x": 1.0, "y": 1.0, "roomName": "Kitchen", "unixTs": 1},
            {"x": 1.2, "y": 1.1, "roomName": "Kitchen", "unixTs": 3},
            {"x": 2.5, "y": 3.0, "roomName": "Office", "unixTs": 7},
        ],
        "d2": [
            {"x": 2.0, "y": 2.0, "roomName": "Office", "unixTs": 10},
            {"x": 2.1, "y": 2.0, "roomName": "Office", "unixTs": 14},
        ],
    }

    def _client(self):
        client = MagicMock()

        def fake_get(path, params=None, **kw):
            if path == "/api/state/devices":
                return [{"id": "d1", "name": "Jon Phone"}, {"id": "d2", "name": "Watch"}]
            if path.startswith("/api/history/"):
                return {"history": self.ROWS[path.rsplit("/", 1)[-1]]}
            raise AssertionError(f"unexpected path {path}")

        client.get.side_effect = fake_get
        return client

    def _run(self, tmp_path, monkeypatch, *args):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        with patch(
            "cli_anything.espresense.espresense_cli.make_client",
            return_value=self._client(),
        ):
            return CliRunner().invoke(cli, ["--config", str(cfg), *args])

    def test_json_aggregates_all_tracked_devices(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, "--json", "history", "heatmap")
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["device_count"] == 2
        assert out["devices_queried"] == 2
        assert out["points"] == 5
        assert out["visits"] == 3
        assert out["seconds"] == 6.0  # kitchen 1-3 (2) + office 7-7 (0) + office 10-14 (4)
        rooms = {r["room"]: r for r in out["rooms"]}
        assert rooms["Kitchen"]["points"] == 2
        assert rooms["Kitchen"]["visits"] == 1
        assert rooms["Office"]["devices"] == ["d1", "d2"]
        # most-used room first
        assert out["rooms"][0]["room"] == "Office"

    def test_fetches_every_tracked_device_by_default(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = self._client()
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            CliRunner().invoke(cli, ["--config", str(cfg), "--json", "history", "heatmap"])
        paths = [c.args[0] for c in client.get.call_args_list]
        assert "/api/state/devices" in paths
        assert "/api/history/d1" in paths and "/api/history/d2" in paths

    def test_device_filter_skips_the_discovery_call(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = self._client()
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(
                cli,
                [
                    "--config",
                    str(cfg),
                    "--json",
                    "history",
                    "heatmap",
                    "--device",
                    "d2",
                ],
            )
        assert result.exit_code == 0
        paths = [c.args[0] for c in client.get.call_args_list]
        assert "/api/state/devices" not in paths
        assert "/api/history/d1" not in paths
        out = json.loads(result.output)
        assert out["device_count"] == 1
        assert out["devices_queried"] == 1
        assert [r["room"] for r in out["rooms"]] == ["Office"]

    def test_limit_is_applied_per_device(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, "--json", "history", "heatmap", "--limit", "1")
        assert result.exit_code == 0
        out = json.loads(result.output)
        # only each device's last point survives the fold
        assert out["points"] == 2
        assert [r["room"] for r in out["rooms"]] == ["Office"]

    def test_empty_history_renders_the_no_data_line(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.side_effect = lambda path, params=None, **kw: (
            [{"id": "d1"}] if path == "/api/state/devices" else {"history": []}
        )
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            result = CliRunner().invoke(cli, ["--config", str(cfg), "history", "heatmap"])
        assert result.exit_code == 0
        assert "no room history" in result.output

    def test_human_output_is_a_usage_table(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, "history", "heatmap")
        assert result.exit_code == 0
        assert "devices: 2 (of 2 queried)" in result.output
        assert "Office" in result.output and "Kitchen" in result.output
        assert "visits" in result.output  # header


class TestRoomUsageWorkflow:
    """`history trail` (one device) and `history heatmap` (the fleet) agree.

    Both fold the same /api/history/<id> rows with the same segment rules, so
    the rooms a per-device trail reports must be a subset of what the heatmap
    attributes to that same device — an agent triangulating "which room does
    this phone actually live in" must never get two different stories.
    """

    ROWS = {
        "d1": [
            {"roomName": "Kitchen", "unixTs": 1},
            {"roomName": "Kitchen", "unixTs": 4},
            {"roomName": "Office", "unixTs": 6},
            {"roomName": "Kitchen", "unixTs": 9},
        ],
        "d2": [{"roomName": "Office", "unixTs": 2}, {"roomName": "Office", "unixTs": 5}],
    }

    def test_trail_rooms_are_attributed_to_the_device_in_the_heatmap(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()

        def fake_get(path, params=None, **kw):
            if path == "/api/state/devices":
                return [{"id": "d1"}, {"id": "d2"}]
            return {"history": self.ROWS[path.rsplit("/", 1)[-1]]}

        client.get.side_effect = fake_get
        with patch("cli_anything.espresense.espresense_cli.make_client", return_value=client):
            trail = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "history", "trail", "d1"]
            )
            heat = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "history", "heatmap"])

        assert trail.exit_code == 0 and heat.exit_code == 0
        trail_out = json.loads(trail.output)
        heat_out = json.loads(heat.output)
        trail_rooms = set(trail_out["rooms_visited"])
        by_room = {r["room"]: r for r in heat_out["rooms"]}
        assert trail_rooms <= set(by_room)
        assert "d1" in by_room["Kitchen"]["devices"]
        assert "d1" in by_room["Office"]["devices"]
        # visit counts agree: d1 enters Kitchen twice
        assert by_room["Kitchen"]["visits"] == 2
        assert by_room["Office"]["visits"] == 2  # d1 once + d2 once


# ── config diff (v0.9.0) ─────────────────────────────────────────────────────

CONFIG_YAML_A = """\
timeout: 30
mqtt:
  url: mqtt://broker:1883
  password: hunter2
floors:
  - id: ground
    name: Ground Floor
    rooms:
      - name: Kitchen
        points: [[0,0],[4,0],[4,4],[0,4]]
"""

CONFIG_YAML_B = """\
timeout: 45
mqtt:
  url: mqtt://broker:1883
  password: hunter2
floors:
  - id: ground
    name: Ground Floor
    rooms:
      - name: Kitchen
        points: [[0,0],[4,0],[4,4],[0,4]]
      - name: Hall
        points: [[4,0],[8,0],[8,4],[4,4]]
"""


class TestConfigDiffE2E:
    """`config diff` — did the companion actually pick up the deployed config?"""

    def _run(self, tmp_path, monkeypatch, args, parsed, client=None, source_kind="k8s"):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        mock_source = MagicMock()
        mock_source.fetch.return_value = ("raw", parsed)
        mock_source.describe.return_value = f"{source_kind}://deployed/config.yaml"
        needs_client = "--against" not in args and client is not None
        with patch(
            "cli_anything.espresense.espresense_cli.make_config_source",
            return_value=mock_source,
        ):
            if needs_client:
                with patch(
                    "cli_anything.espresense.espresense_cli.make_client", return_value=client
                ):
                    return CliRunner().invoke(cli, ["--config", str(cfg), *args])
            return CliRunner().invoke(cli, ["--config", str(cfg), *args])

    def test_json_identical_when_companion_runs_the_deployed_config(self, tmp_path, monkeypatch):
        parsed = {"timeout": 30, "floors": [{"id": "ground"}]}
        client = MagicMock()
        client.get.return_value = parsed
        result = self._run(tmp_path, monkeypatch, ["--json", "config", "diff"], parsed, client)
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["identical"] is True
        assert out["difference_count"] == 0
        assert out["differences"] == []
        assert "left" in out and "right" in out

    def test_json_reports_drift_and_exits_1(self, tmp_path, monkeypatch):
        running = {"timeout": 30}
        deployed = {"timeout": 45, "locators": {"nelder_mead": {"enabled": True}}}
        client = MagicMock()
        client.get.return_value = running
        result = self._run(tmp_path, monkeypatch, ["--json", "config", "diff"], deployed, client)
        assert result.exit_code == 1  # drift is gate-able, like `rooms overlaps`
        out = json.loads(result.output)
        assert out["identical"] is False
        assert out["difference_count"] == 2
        paths = {d["path"]: d["kind"] for d in out["differences"]}
        assert paths == {"timeout": "changed", "locators": "added"}

    def test_json_push_without_restart_is_detected_as_added(self, tmp_path, monkeypatch):
        # the exact gotcha: config.yaml gained a room but the companion was
        # never restarted, so its running view still lacks it
        running = {"floors": [{"id": "ground", "rooms": [{"name": "Kitchen"}]}]}
        deployed = {
            "floors": [
                {"id": "ground", "rooms": [{"name": "Kitchen"}, {"name": "Hall"}]},
            ]
        }
        client = MagicMock()
        client.get.return_value = running
        result = self._run(tmp_path, monkeypatch, ["--json", "config", "diff"], deployed, client)
        out = json.loads(result.output)
        assert out["differences"] == [
            {
                "path": "floors[0].rooms[1]",
                "kind": "added",
                "old": None,
                "new": {"name": "Hall"},
            }
        ]

    def test_secrets_are_redacted_in_json_output(self, tmp_path, monkeypatch):
        running = {"mqtt": {"password": "hunter2"}}
        deployed = {"mqtt": {"password": "newpass"}}
        client = MagicMock()
        client.get.return_value = running
        result = self._run(tmp_path, monkeypatch, ["--json", "config", "diff"], deployed, client)
        out = json.loads(result.output)
        diff = out["differences"][0]
        assert diff["path"] == "mqtt.password"
        assert diff["old"] == "***" and diff["new"] == "***"

    def test_human_output_lists_differences(self, tmp_path, monkeypatch):
        client = MagicMock()
        client.get.return_value = {"timeout": 30}
        result = self._run(tmp_path, monkeypatch, ["config", "diff"], {"timeout": 45}, client)
        assert result.exit_code == 1
        assert "left:" in result.output and "right:" in result.output
        assert "~ timeout: 30 -> 45" in result.output
        assert "1 difference(s)" in result.output

    def test_human_output_identical_message(self, tmp_path, monkeypatch):
        parsed = {"timeout": 30}
        client = MagicMock()
        client.get.return_value = parsed
        result = self._run(tmp_path, monkeypatch, ["config", "diff"], parsed, client)
        assert result.exit_code == 0
        assert "no differences" in result.output

    def test_offline_two_file_comparison_needs_no_companion(self, tmp_path, monkeypatch):
        a = tmp_path / "draft.yaml"
        b = tmp_path / "deployed.yaml"
        a.write_text(CONFIG_YAML_A)
        b.write_text(CONFIG_YAML_B)
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        mock_source = MagicMock()
        mock_source.fetch.return_value = ("raw", yaml_io.load(CONFIG_YAML_B))
        mock_source.describe.return_value = f"file://{b}"
        with patch(
            "cli_anything.espresense.espresense_cli.make_config_source",
            return_value=mock_source,
        ):
            result = CliRunner().invoke(
                cli,
                ["--config", str(cfg), "--json", "config", "diff", "--against", str(a)],
            )
        assert result.exit_code == 1
        out = json.loads(result.output)
        assert out["left"] == f"file://{a}"
        paths = {d["path"]: d["kind"] for d in out["differences"]}
        assert paths == {
            "timeout": "changed",
            "floors[0].rooms[1]": "added",
        }

    def test_companion_unreachable_fails_cleanly(self, tmp_path, monkeypatch):
        from cli_anything.espresense.utils.companion_client import CompanionError

        client = MagicMock()
        client.get.side_effect = CompanionError("connection refused")
        result = self._run(tmp_path, monkeypatch, ["config", "diff"], {"timeout": 1}, client)
        assert result.exit_code == 1
        assert "cannot read the companion's running config" in result.output
        assert "Traceback" not in result.output

    def test_help_works(self):
        result = CliRunner().invoke(cli, ["config", "diff", "--help"])
        assert result.exit_code == 0
        assert "--against" in result.output


class TestConfigDiffWorkflow:
    """`config diff` closes the push → verify loop.

    An agent that pushes config.yaml must be able to prove the companion
    picked it up: after a push with --restart the running view matches, and
    after a push WITHOUT --restart it does not. Both stories must come from
    the same command, and the drift report must agree with what `companion
    config-get` shows.
    """

    NEW = {"timeout": 45, "rooms": [{"name": "Kitchen"}]}

    def _invoke(self, tmp_path, monkeypatch, running, deployed, extra=()):
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        mock_source = MagicMock()
        mock_source.fetch.return_value = ("raw", deployed)
        mock_source.describe.return_value = "k8s://espresense/companion/config.yaml"
        client = MagicMock()
        client.get.return_value = running
        with (
            patch(
                "cli_anything.espresense.espresense_cli.make_config_source",
                return_value=mock_source,
            ),
            patch("cli_anything.espresense.espresense_cli.make_client", return_value=client),
        ):
            return CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "config", "diff", *extra]
            )

    def test_after_push_with_restart_the_views_match(self, tmp_path, monkeypatch):
        result = self._invoke(tmp_path, monkeypatch, self.NEW, self.NEW)
        assert result.exit_code == 0
        assert json.loads(result.output)["identical"] is True

    def test_after_push_without_restart_drift_is_reported(self, tmp_path, monkeypatch):
        result = self._invoke(tmp_path, monkeypatch, {"timeout": 30}, self.NEW)
        assert result.exit_code == 1
        out = json.loads(result.output)
        assert out["identical"] is False
        assert {d["path"] for d in out["differences"]} == {"timeout", "rooms"}

    def test_companion_config_get_tells_the_same_story_as_the_diff(self, tmp_path, monkeypatch):
        """The diff's left side IS `companion config-get`'s payload."""
        running = {"timeout": 30}
        deployed = {"timeout": 45}
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", cfg)
        client = MagicMock()
        client.get.return_value = running
        mock_source = MagicMock()
        mock_source.fetch.return_value = ("raw", deployed)
        mock_source.describe.return_value = "k8s://espresense/companion/config.yaml"
        with (
            patch("cli_anything.espresense.espresense_cli.make_client", return_value=client),
            patch(
                "cli_anything.espresense.espresense_cli.make_config_source",
                return_value=mock_source,
            ),
        ):
            diff = CliRunner().invoke(cli, ["--config", str(cfg), "--json", "config", "diff"])
            config_get = CliRunner().invoke(
                cli, ["--config", str(cfg), "--json", "companion", "config-get"]
            )
        assert diff.exit_code == 1
        assert config_get.exit_code == 0
        assert json.loads(config_get.output) == running
        assert json.loads(diff.output)["differences"][0]["old"] == running["timeout"]
