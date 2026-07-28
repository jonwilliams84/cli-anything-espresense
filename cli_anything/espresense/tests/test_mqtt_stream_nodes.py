"""Tests for mqtt, stream, and nodes modules — uncovered real logic.

Covers payload serialization in mqtt.publish_setting/publish_raw, the
client-creation error path, watch() message collection and callback
logging; stream._ws_url scheme conversion, event type filtering, JSON
decode fallback, and the missing-dependency error; nodes.list_live_nodes
telemetry extraction and nodes.merged_view join semantics including
live-only autodiscovered nodes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cli_anything.espresense.core import mqtt, nodes
from cli_anything.espresense.core import stream as stream_mod
from cli_anything.espresense.core.stream import _ws_url, stream


# ── mqtt._client ─────────────────────────────────────────────────────────────


class TestMqttClient:
    def test_client_raises_when_paho_missing(self):
        """If paho-mqtt is not installed, _client must raise MqttError."""
        with patch.object(mqtt, "mqtt", None):
            with pytest.raises(mqtt.MqttError, match="paho-mqtt is not installed"):
                mqtt._client("localhost")

    def test_client_sets_credentials_when_username_provided(self):
        """username_pw_set must be called when username is given."""
        fake_client = MagicMock()
        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client
            mqtt._client("broker.local", username="user", password="secret")
        fake_client.username_pw_set.assert_called_once_with("user", "secret")

    def test_client_omits_credentials_when_no_username(self):
        """username_pw_set must NOT be called when username is None."""
        fake_client = MagicMock()
        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client
            mqtt._client("broker.local")
        fake_client.username_pw_set.assert_not_called()

    def test_client_connects_with_host_port(self):
        """connect() must receive the host and port."""
        fake_client = MagicMock()
        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client
            mqtt._client("broker.local", port=8883)
        fake_client.connect.assert_called_once()
        args, kwargs = fake_client.connect.call_args
        assert args[0] == "broker.local"
        assert args[1] == 8883


# ── mqtt.publish_setting ─────────────────────────────────────────────────────


class TestPublishSetting:
    """publish_setting serializes different value types into MQTT payloads."""

    def _patch_client(self):
        fake_client = MagicMock()
        fake_info = MagicMock()
        fake_info.rc = 0
        fake_client.publish.return_value = fake_info
        mock_mqtt = patch.object(mqtt, "mqtt")
        mock_mqtt_obj = mock_mqtt.start()
        mock_mqtt_obj.Client.return_value = fake_client
        return fake_client, fake_info, mock_mqtt

    def teardown_method(self, method):
        patch.stopall()

    def test_bool_true_becomes_string_true(self):
        fake_client, fake_info, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "enabled", True)
        assert result["payload"] == "true"

    def test_bool_false_becomes_string_false(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "enabled", False)
        assert result["payload"] == "false"

    def test_int_becomes_string(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "count", 42)
        assert result["payload"] == "42"

    def test_float_becomes_string(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "factor", 3.14)
        assert result["payload"] == "3.14"

    def test_dict_becomes_json(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "config", {"a": 1})
        assert json.loads(result["payload"]) == {"a": 1}

    def test_list_becomes_json(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "items", [1, 2, 3])
        assert json.loads(result["payload"]) == [1, 2, 3]

    def test_string_passes_through(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "label", "hello")
        assert result["payload"] == "hello"

    def test_topic_uses_prefix(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "key", "val", prefix="custom")
        assert result["topic"] == "custom/rooms/node1/key/set"

    def test_default_topic_uses_espresense_prefix(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "key", "val")
        assert result["topic"] == "espresense/rooms/node1/key/set"

    def test_publish_called_with_retain(self):
        fake_client, _, _ = self._patch_client()
        mqtt.publish_setting("host", "node1", "key", "val", retain=True)
        _, kwargs = fake_client.publish.call_args
        assert kwargs["retain"] is True

    def test_publish_called_without_retain(self):
        fake_client, _, _ = self._patch_client()
        mqtt.publish_setting("host", "node1", "key", "val", retain=False)
        _, kwargs = fake_client.publish.call_args
        assert kwargs["retain"] is False

    def test_result_includes_rc(self):
        fake_client, fake_info, _ = self._patch_client()
        result = mqtt.publish_setting("host", "node1", "key", "val")
        assert result["rc"] == 0

    def test_disconnect_called_after_publish(self):
        fake_client, _, _ = self._patch_client()
        mqtt.publish_setting("host", "node1", "key", "val")
        fake_client.disconnect.assert_called_once()
        fake_client.loop_stop.assert_called_once()


# ── mqtt.publish_raw ─────────────────────────────────────────────────────────


class TestPublishRaw:
    """publish_raw serializes payloads and publishes to an arbitrary topic."""

    def _patch_client(self):
        fake_client = MagicMock()
        fake_info = MagicMock()
        fake_info.rc = 0
        fake_client.publish.return_value = fake_info
        mock_mqtt = patch.object(mqtt, "mqtt")
        mock_mqtt_obj = mock_mqtt.start()
        mock_mqtt_obj.Client.return_value = fake_client
        return fake_client, fake_info, mock_mqtt

    def teardown_method(self, method):
        patch.stopall()

    def test_dict_payload_becomes_json(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "some/topic", {"x": 1})
        assert json.loads(result["payload"]) == {"x": 1}
        assert result["topic"] == "some/topic"

    def test_bool_payload_becomes_string(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "t", True)
        assert result["payload"] == "true"

    def test_int_payload_becomes_string(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "t", 99)
        assert result["payload"] == "99"

    def test_float_payload_becomes_string(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "t", 2.5)
        assert result["payload"] == "2.5"

    def test_string_payload_passes_through(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "t", "plain")
        assert result["payload"] == "plain"

    def test_list_payload_becomes_json(self):
        fake_client, _, _ = self._patch_client()
        result = mqtt.publish_raw("host", "t", [1, 2])
        assert json.loads(result["payload"]) == [1, 2]

    def test_retain_flag_passed_through(self):
        fake_client, _, _ = self._patch_client()
        mqtt.publish_raw("host", "t", "x", retain=True)
        _, kwargs = fake_client.publish.call_args
        assert kwargs["retain"] is True

    def test_disconnect_called_after_publish(self):
        fake_client, _, _ = self._patch_client()
        mqtt.publish_raw("host", "t", "x")
        fake_client.disconnect.assert_called_once()


# ── mqtt.watch ───────────────────────────────────────────────────────────────


class TestMqttWatch:
    """watch() subscribes, collects messages, and logs callback failures."""

    def _run_watch_with_messages(self, messages, callback=None, caplog=None):
        """Helper: patch mqtt, inject messages via time.sleep, return results."""
        fake_client = MagicMock()
        captured_handler = []

        # Capture the on_message assignment by intercepting __setattr__
        original_setattr = MagicMock.__setattr__

        def _capture_setattr(obj, name, value):
            if name == "on_message":
                captured_handler.append(value)
            original_setattr(obj, name, value)

        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client

            call_count = [0]

            def _sleep_and_inject(seconds):
                if call_count[0] < len(messages):
                    msg = MagicMock()
                    msg.topic = messages[call_count[0]][0]
                    msg.payload = messages[call_count[0]][1]
                    captured_handler[0](None, None, msg)
                    call_count[0] += 1

            with patch.object(mqtt.time, "sleep", _sleep_and_inject):
                with patch("unittest.mock.MagicMock.__setattr__", _capture_setattr):
                    if caplog is not None:
                        import logging

                        with caplog.at_level(
                            logging.WARNING,
                            logger="cli_anything.espresense.core.mqtt",
                        ):
                            result = mqtt.watch(
                                "host", "espresense/#", duration=0.01, callback=callback
                            )
                    else:
                        result = mqtt.watch(
                            "host", "espresense/#", duration=0.01, callback=callback
                        )

        return result, fake_client

    def test_watch_subscribes_to_topic_filter(self):
        """watch must call subscribe with the given topic filter."""
        fake_client = MagicMock()
        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client
            result = mqtt.watch("host", "espresense/devices/#", duration=0.01)

        fake_client.subscribe.assert_called_once_with("espresense/devices/#", qos=0)
        assert result == []

    def test_watch_disconnects_after_duration(self):
        """watch must stop the loop and disconnect when duration expires."""
        fake_client = MagicMock()
        with patch.object(mqtt, "mqtt") as mock_mqtt:
            mock_mqtt.Client.return_value = fake_client
            mqtt.watch("host", "espresense/#", duration=0.01)

        fake_client.loop_stop.assert_called_once()
        fake_client.disconnect.assert_called_once()

    def test_watch_message_handler_decodes_payload(self):
        """The on_message handler must decode bytes payload to string."""
        result, _ = self._run_watch_with_messages(
            [
                ("espresense/devices/abc", b'{"id":1}'),
            ]
        )
        assert len(result) == 1
        assert result[0]["topic"] == "espresense/devices/abc"
        assert result[0]["payload"] == '{"id":1}'
        assert "ts" in result[0]

    def test_watch_callback_failure_is_logged(self, caplog):
        """A raising callback must be logged, not silently swallowed."""

        def failing_callback(topic, payload):
            raise RuntimeError("callback boom")

        result, _ = self._run_watch_with_messages(
            [("espresense/test", b"data")],
            callback=failing_callback,
            caplog=caplog,
        )

        # Message was still collected despite callback failure
        assert len(result) == 1
        # The failure was logged
        assert any("callback failed" in r.message for r in caplog.records), (
            "a raising callback must be logged, not silently discarded"
        )

    def test_watch_invokes_callback_on_success(self):
        """A non-raising callback must be invoked with topic and payload."""
        callback_calls = []

        def good_callback(topic, payload):
            callback_calls.append((topic, payload))

        self._run_watch_with_messages(
            [("espresense/ok", b"hello")],
            callback=good_callback,
        )

        assert len(callback_calls) == 1
        assert callback_calls[0] == ("espresense/ok", "hello")

    def test_watch_collects_multiple_messages(self):
        """Multiple messages should all be collected in order."""
        result, _ = self._run_watch_with_messages(
            [
                ("t/a", b"1"),
                ("t/b", b"2"),
                ("t/c", b"3"),
            ]
        )
        assert len(result) == 3
        assert result[0]["payload"] == "1"
        assert result[2]["payload"] == "3"


# ── stream._ws_url ───────────────────────────────────────────────────────────


class TestWsUrl:
    def test_http_becomes_ws(self):
        assert _ws_url("http://localhost:8267") == "ws://localhost:8267/ws"

    def test_https_becomes_wss(self):
        assert _ws_url("https://companion.example.com") == "wss://companion.example.com/ws"

    def test_show_all_adds_query(self):
        assert _ws_url("http://host:80", show_all=True) == "ws://host:80/ws?showAll=true"

    def test_no_show_all_no_query(self):
        assert "?showAll" not in _ws_url("http://host:80", show_all=False)

    def test_url_with_path_uses_netloc(self):
        """A URL with a path should use only the netloc for the ws URL."""
        result = _ws_url("http://host:8080/some/path")
        assert result == "ws://host:8080/ws"


# ── stream.stream ────────────────────────────────────────────────────────────


class TestStream:
    """stream() connects, collects events, filters by type, handles errors."""

    def _make_mock_websocket(self, recv_side_effect, timeout_exc_class=None):
        """Create a mock websocket module with a fake connection.

        If recv_side_effect is a list, it is wrapped so that after the list
        is exhausted, recv() returns empty strings (which stream() skips)
        until the duration expires. This prevents StopIteration from
        aborting the test.
        """
        fake_ws = MagicMock()
        fake_ws.close = MagicMock()

        if isinstance(recv_side_effect, list):
            _iter = iter(recv_side_effect)

            def _recv():
                try:
                    return next(_iter)
                except StopIteration:
                    return ""

            fake_ws.recv.side_effect = _recv
        else:
            fake_ws.recv.side_effect = recv_side_effect

        mock_websocket = MagicMock()
        mock_websocket.create_connection.return_value = fake_ws
        mock_websocket.WebSocketTimeoutException = timeout_exc_class or Exception
        return mock_websocket, fake_ws

    def test_stream_raises_when_websocket_missing(self):
        """If websocket-client is not installed, stream must raise RuntimeError."""
        with patch.object(stream_mod, "websocket", None):
            with pytest.raises(RuntimeError, match="websocket-client is not installed"):
                stream("http://localhost:8267", duration=1)

    def test_stream_collects_events_with_duration(self):
        """stream should collect JSON events from the WebSocket."""
        events = [
            json.dumps({"type": "deviceChanged", "id": "dev1"}),
            json.dumps({"type": "nodeStateChanged", "id": "node1"}),
        ]
        mock_ws, fake_ws = self._make_mock_websocket(events)
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267", duration=0.01)

        assert len(result) == 2
        assert result[0]["type"] == "deviceChanged"
        assert result[1]["type"] == "nodeStateChanged"

    def test_stream_filters_by_type(self):
        """When types is specified, only matching events are collected."""
        events = [
            json.dumps({"type": "deviceChanged", "id": "dev1"}),
            json.dumps({"type": "nodeStateChanged", "id": "node1"}),
            json.dumps({"type": "deviceChanged", "id": "dev2"}),
        ]
        mock_ws, _ = self._make_mock_websocket(events)
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream(
                "http://localhost:8267",
                duration=0.01,
                types={"deviceChanged"},
            )

        assert len(result) == 2
        assert all(e["type"] == "deviceChanged" for e in result)

    def test_stream_invalid_json_becomes_raw_event(self):
        """Non-JSON messages should be captured as {'raw': <message>}."""
        mock_ws, _ = self._make_mock_websocket(["not json at all"])
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267", duration=0.01)

        assert len(result) == 1
        assert result[0]["raw"] == "not json at all"

    def test_stream_empty_messages_skipped(self):
        """Empty string messages should be skipped, not collected."""
        mock_ws, _ = self._make_mock_websocket(["", json.dumps({"type": "x"})])
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267", duration=0.01)

        assert len(result) == 1
        assert result[0]["type"] == "x"

    def test_stream_timeout_continues_loop(self):
        """WebSocketTimeoutException should not abort; loop continues."""
        timeout_exc = type("Timeout", (Exception,), {})

        # First call raises timeout, second returns a valid event,
        # subsequent calls keep raising timeout until duration expires.
        call_count = [0]

        def _recv_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise timeout_exc("timed out")
            elif call_count[0] == 2:
                return json.dumps({"type": "deviceChanged"})
            else:
                raise timeout_exc("timed out")

        mock_ws, _ = self._make_mock_websocket(
            _recv_side_effect,
            timeout_exc_class=timeout_exc,
        )
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream("http://localhost:8267", duration=0.05)

        assert len(result) == 1
        assert result[0]["type"] == "deviceChanged"

    def test_stream_callback_invoked(self):
        """The callback should be called for each collected event."""
        mock_ws, _ = self._make_mock_websocket([json.dumps({"type": "x", "val": 1})])

        callback_calls = []

        def cb(event):
            callback_calls.append(event)

        with patch.object(stream_mod, "websocket", mock_ws):
            stream("http://localhost:8267", duration=0.01, callback=cb)

        assert len(callback_calls) == 1
        assert callback_calls[0]["val"] == 1

    def test_stream_callback_exception_logged_not_raised(self, caplog):
        """A raising callback must be logged, not propagated."""
        import logging

        mock_ws, _ = self._make_mock_websocket([json.dumps({"type": "x"})])

        def bad_callback(event):
            raise ValueError("callback error")

        with patch.object(stream_mod, "websocket", mock_ws):
            with caplog.at_level(logging.WARNING, logger="cli_anything.espresense.core.stream"):
                # Should not raise
                result = stream("http://localhost:8267", duration=0.01, callback=bad_callback)

        # Event was still collected
        assert len(result) == 1
        # Failure was logged
        assert any("stream callback" in r.message for r in caplog.records)

    def test_stream_closes_websocket(self):
        """The WebSocket must be closed in the finally block."""
        mock_ws, fake_ws = self._make_mock_websocket([json.dumps({"type": "x"})])
        with patch.object(stream_mod, "websocket", mock_ws):
            stream("http://localhost:8267", duration=0.01)

        fake_ws.close.assert_called_once()

    def test_stream_show_all_passed_to_url(self):
        """show_all=True should produce a URL with ?showAll=true."""
        events = [json.dumps({"type": "x"})]
        mock_ws, _ = self._make_mock_websocket(events)
        with patch.object(stream_mod, "websocket", mock_ws):
            stream("http://localhost:8267", show_all=True, duration=0.01)

        # Verify the URL passed to create_connection includes showAll=true
        call_args = mock_ws.create_connection.call_args
        assert "?showAll=true" in call_args[0][0]

    def test_stream_types_filter_excludes_non_matching(self):
        """Events whose type is not in the types set must be excluded."""
        events = [
            json.dumps({"type": "deviceChanged"}),
            json.dumps({"type": "unknown"}),
            json.dumps({"type": "nodeStateChanged"}),
        ]
        mock_ws, _ = self._make_mock_websocket(events)
        with patch.object(stream_mod, "websocket", mock_ws):
            result = stream(
                "http://localhost:8267",
                duration=0.01,
                types={"deviceChanged", "nodeStateChanged"},
            )

        assert len(result) == 2
        types_collected = {e["type"] for e in result}
        assert "unknown" not in types_collected


# ── nodes.list_live_nodes ────────────────────────────────────────────────────


class TestListLiveNodes:
    """list_live_nodes extracts telemetry and location fields from raw API data."""

    def test_extracts_telemetry_and_location(self):
        raw = [
            {
                "id": "node1",
                "name": "living-room",
                "online": True,
                "telemetry": {
                    "ip": "192.168.1.10",
                    "uptime": 3600,
                    "rssi": -55,
                    "firmware": "3.3",
                    "version": "1.0",
                    "freeHeap": 50000,
                },
                "location": {"x": 1.5, "y": 2.5, "z": 1.0},
                "floors": ["ground"],
                "sourceType": "mqtt",
            }
        ]
        client = MagicMock()
        with patch.object(nodes.companion_api, "list_nodes", return_value=raw):
            result = nodes.list_live_nodes(client)

        assert len(result) == 1
        row = result[0]
        assert row["id"] == "node1"
        assert row["name"] == "living-room"
        assert row["online"] is True
        assert row["ip"] == "192.168.1.10"
        assert row["uptime"] == 3600
        assert row["rssi"] == -55
        assert row["firmware"] == "3.3"
        assert row["version"] == "1.0"
        assert row["free_heap"] == 50000
        assert row["floors"] == ["ground"]
        assert row["x"] == 1.5
        assert row["y"] == 2.5
        assert row["z"] == 1.0
        assert row["source"] == "mqtt"

    def test_handles_missing_telemetry_and_location(self):
        """Nodes without telemetry or location should not crash."""
        raw = [{"id": "node2", "name": "bare-node", "online": False}]
        client = MagicMock()
        with patch.object(nodes.companion_api, "list_nodes", return_value=raw):
            result = nodes.list_live_nodes(client)

        assert len(result) == 1
        row = result[0]
        assert row["id"] == "node2"
        assert row["online"] is False
        assert row["ip"] is None
        assert row["rssi"] is None
        assert row["x"] is None
        assert row["floors"] == []

    def test_include_telemetry_passed_through(self):
        """The include_telemetry flag must be forwarded to companion_api.list_nodes."""
        client = MagicMock()
        with patch.object(nodes.companion_api, "list_nodes", return_value=[]) as mock_list:
            nodes.list_live_nodes(client, include_telemetry=False)
        mock_list.assert_called_once_with(client, include_telemetry=False)

    def test_empty_live_nodes(self):
        """An empty list from the API should produce an empty result."""
        client = MagicMock()
        with patch.object(nodes.companion_api, "list_nodes", return_value=[]):
            result = nodes.list_live_nodes(client)
        assert result == []


# ── nodes.merged_view ────────────────────────────────────────────────────────


class TestMergedView:
    """merged_view joins config rows with live state by name."""

    def test_config_node_merged_with_live(self):
        parsed = {
            "nodes": [
                {"name": "kitchen", "room": "Kitchen", "point": [1, 2, 3], "floors": ["g"]},
            ]
        }
        live = [
            {
                "name": "kitchen",
                "online": True,
                "ip": "10.0.0.1",
                "rssi": -50,
                "uptime": 100,
                "firmware": "3.3",
                "version": "1.0",
                "source": "mqtt",
            }
        ]
        result = nodes.merged_view(parsed, live)
        assert len(result) == 1
        row = result[0]
        assert row["name"] == "kitchen"
        assert row["room"] == "Kitchen"
        assert row["online"] is True
        assert row["ip"] == "10.0.0.1"
        assert row["rssi"] == -50
        assert row["source"] == "mqtt"

    def test_config_node_without_live_state(self):
        """A config node with no matching live row keeps config fields, live=None."""
        parsed = {"nodes": [{"name": "offline-node", "room": "Hall"}]}
        result = nodes.merged_view(parsed, [])
        assert len(result) == 1
        assert result[0]["name"] == "offline-node"
        assert result[0]["room"] == "Hall"
        assert result[0]["online"] is None
        assert result[0]["ip"] is None

    def test_live_only_node_appended(self):
        """Nodes that exist live but not in config should be appended."""
        parsed = {"nodes": []}
        live = [
            {
                "name": "autodiscovered",
                "online": True,
                "ip": "10.0.0.99",
                "rssi": -60,
                "floors": ["ground"],
                "x": 5.0,
                "y": 6.0,
                "z": 7.0,
            }
        ]
        result = nodes.merged_view(parsed, live)
        assert len(result) == 1
        row = result[0]
        assert row["name"] == "autodiscovered"
        assert row["room"] is None
        assert row["online"] is True
        assert row["ip"] == "10.0.0.99"
        assert row["point"] == [5.0, 6.0, 7.0]
        assert row["source"] == "Live"

    def test_live_only_node_with_explicit_source_keeps_it(self):
        """A live-only node that already has a source should keep it."""
        parsed = {"nodes": []}
        live = [{"name": "manual", "online": True, "source": "manual"}]
        result = nodes.merged_view(parsed, live)
        assert result[0]["source"] == "manual"

    def test_mixed_config_and_live_only(self):
        """Both config-matched and live-only nodes appear in the result."""
        parsed = {
            "nodes": [
                {"name": "known", "room": "Room A", "point": [0, 0, 0]},
            ]
        }
        live = [
            {"name": "known", "online": True, "ip": "1.2.3.4"},
            {"name": "unknown", "online": False, "floors": ["f2"]},
        ]
        result = nodes.merged_view(parsed, live)
        assert len(result) == 2
        names = [r["name"] for r in result]
        assert "known" in names
        assert "unknown" in names
        # known has config room, unknown has None
        known = next(r for r in result if r["name"] == "known")
        unknown = next(r for r in result if r["name"] == "unknown")
        assert known["room"] == "Room A"
        assert unknown["room"] is None
        assert unknown["source"] == "Live"

    def test_empty_config_and_empty_live(self):
        """No config nodes and no live nodes should produce an empty list."""
        result = nodes.merged_view({"nodes": []}, [])
        assert result == []

    def test_no_nodes_key_in_config(self):
        """A parsed config with no 'nodes' key should not crash."""
        result = nodes.merged_view({}, [])
        assert result == []

    def test_config_node_defaults_enabled_and_stationary(self):
        """Config nodes without enabled/stationary should default to True."""
        parsed = {"nodes": [{"name": "n", "room": "R"}]}
        result = nodes.merged_view(parsed, [])
        assert result[0]["enabled"] is True
        assert result[0]["stationary"] is True
