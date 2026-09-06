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
