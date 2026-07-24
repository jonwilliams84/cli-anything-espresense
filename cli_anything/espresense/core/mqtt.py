"""MQTT pub/sub helpers for the ESPresense topic family.

Use this when you want to bypass both the companion's REST API and the
per-node HTTP UI and talk to nodes (or the companion) directly via the
broker — e.g. for bulk setting pushes or live device-distance watching.

Topic prefix is `espresense` by default; passed in as a constructor arg so
deployments using a non-standard prefix still work.

Setting topics (publish → retained):
  espresense/rooms/<id>/<key>/set            ← per-node setting
  espresense/settings/<device-id>/config     ← per-device fingerprint

Telemetry topics (subscribe):
  espresense/rooms/<id>/telemetry
  espresense/rooms/<id>/status
  espresense/devices/<device-id>/<node-id>
  espresense/companion/<device-id>
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional at install time
    mqtt = None  # type: ignore

logger = logging.getLogger(__name__)


class MqttError(RuntimeError):
    pass


def _client(host: str, port: int = 1883, *,
            username: Optional[str] = None,
            password: Optional[str] = None,
            client_id: str = "cli-anything-espresense") -> "mqtt.Client":
    if mqtt is None:
        raise MqttError(
            "paho-mqtt is not installed — pip install paho-mqtt or reinstall "
            "the harness (it's a declared dependency)."
        )
    c = mqtt.Client(client_id=client_id)
    if username:
        c.username_pw_set(username, password or None)
    c.connect(host, port, keepalive=30)
    return c


def publish_setting(host: str, node_id: str, key: str, value, *,
                    port: int = 1883, username: Optional[str] = None,
                    password: Optional[str] = None, prefix: str = "espresense",
                    retain: bool = True) -> dict:
    """Publish a per-node setting and disconnect.

    `value` is stringified — bools become "true"/"false", others use str().
    """
    if isinstance(value, bool):
        payload = "true" if value else "false"
    elif isinstance(value, (int, float)):
        payload = str(value)
    elif isinstance(value, (dict, list)):
        payload = json.dumps(value)
    else:
        payload = str(value)
    topic = f"{prefix}/rooms/{node_id}/{key}/set"
    c = _client(host, port, username=username, password=password)
    try:
        c.loop_start()
        info = c.publish(topic, payload, qos=0, retain=retain)
        info.wait_for_publish(timeout=5)
        return {"topic": topic, "payload": payload, "rc": info.rc}
    finally:
        c.loop_stop()
        c.disconnect()


def publish_raw(host: str, topic: str, payload, *, port: int = 1883,
                username: Optional[str] = None, password: Optional[str] = None,
                retain: bool = False) -> dict:
    """Publish an arbitrary topic — useful for /settings/+/config writes."""
    if isinstance(payload, (dict, list)):
        payload_str = json.dumps(payload)
    elif isinstance(payload, bool):
        payload_str = "true" if payload else "false"
    elif isinstance(payload, (int, float)):
        payload_str = str(payload)
    else:
        payload_str = str(payload)
    c = _client(host, port, username=username, password=password)
    try:
        c.loop_start()
        info = c.publish(topic, payload_str, qos=0, retain=retain)
        info.wait_for_publish(timeout=5)
        return {"topic": topic, "payload": payload_str, "rc": info.rc}
    finally:
        c.loop_stop()
        c.disconnect()


def watch(host: str, topic_filter: str, *, port: int = 1883,
          username: Optional[str] = None, password: Optional[str] = None,
          duration: Optional[float] = None,
          callback: Optional[Callable[[str, str], None]] = None) -> list[dict]:
    """Subscribe to a topic filter and collect/print messages.

    If `duration` is None, runs until KeyboardInterrupt. Returns a list of
    {topic, payload, ts} dicts collected during the watch.
    """
    collected: list[dict] = []

    def _on_msg(_client, _ud, msg):
        rec = {
            "topic": msg.topic,
            "payload": msg.payload.decode("utf-8", errors="replace"),
            "ts": time.time(),
        }
        collected.append(rec)
        if callback:
            try:
                callback(rec["topic"], rec["payload"])
            except Exception:
                logger.exception("MQTT watch callback failed")

    c = _client(host, port, username=username, password=password,
                client_id=f"cli-anything-espresense-watch-{int(time.time())}")
    c.on_message = _on_msg
    c.subscribe(topic_filter, qos=0)
    c.loop_start()
    try:
        if duration is None:
            try:
                while True:
                    time.sleep(0.25)
            except KeyboardInterrupt:
                pass
        else:
            end = time.time() + duration
            while time.time() < end:
                time.sleep(0.1)
    finally:
        c.loop_stop()
        c.disconnect()
    return collected
