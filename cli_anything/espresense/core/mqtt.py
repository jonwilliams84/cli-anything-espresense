"""MQTT pub/sub helpers for the ESPresense topic family.

Use this when you want to bypass both the companion's REST API and the
per-node HTTP UI and talk to nodes (or the companion) directly via the
broker — e.g. for bulk setting pushes or live device-distance watching.

Topic prefix is `espresense` by default; passed in as a constructor arg so
deployments using a non-standard prefix still work.

Setting topics (publish → retained):
  espresense/rooms/<id>/<key>/set            ← per-node setting
  espresense/settings/<key>/set              ← global setting
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


class MqttError(RuntimeError):
    pass


def _client(
    host: str,
    port: int = 1883,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    client_id: str = "cli-anything-espresense",
) -> "mqtt.Client":
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


def _stringify(value) -> str:
    """MQTT payloads are strings — bools become "true"/"false", containers JSON."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def publish_setting(
    host: str,
    node_id: str,
    key: str,
    value,
    *,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
    prefix: str = "espresense",
    retain: bool = True,
) -> dict:
    """Publish a per-node setting and disconnect.

    `value` is stringified — bools become "true"/"false", others use str().
    """
    payload = _stringify(value)
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


def publish_global_setting(
    host: str,
    key: str,
    value,
    *,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
    prefix: str = "espresense",
    retain: bool = True,
) -> dict:
    """Publish a global setting: espresense/settings/<key>/set.

    The deployment-wide counterpart to `publish_setting` (per-node) and
    `publish_device_config` (per-device). Retained by default so nodes and
    the companion re-apply the value at startup.

    `value` is stringified the same way as everywhere else in this module;
    a JSON object such as the `gps` origin passes through as its JSON text.
    """
    if not str(key).strip() or "/" in str(key):
        raise MqttError(f"setting key must be non-empty and contain no '/': {key!r}")
    payload = _stringify(value)
    topic = f"{prefix}/settings/{key}/set"
    c = _client(host, port, username=username, password=password)
    try:
        c.loop_start()
        info = c.publish(topic, payload, qos=0, retain=retain)
        info.wait_for_publish(timeout=5)
        return {"topic": topic, "payload": payload, "rc": info.rc}
    finally:
        c.loop_stop()
        c.disconnect()


def publish_raw(
    host: str,
    topic: str,
    payload,
    *,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
    retain: bool = False,
) -> dict:
    """Publish an arbitrary topic — useful for /settings/+/config writes."""
    payload_str = _stringify(payload)
    c = _client(host, port, username=username, password=password)
    try:
        c.loop_start()
        info = c.publish(topic, payload_str, qos=0, retain=retain)
        info.wait_for_publish(timeout=5)
        return {"topic": topic, "payload": payload_str, "rc": info.rc}
    finally:
        c.loop_stop()
        c.disconnect()


def watch(
    host: str,
    topic_filter: str,
    *,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
    duration: Optional[float] = None,
    callback: Optional[Callable[[str, str], None]] = None,
) -> list[dict]:
    """Subscribe to a topic filter and collect/print messages.

    If `duration` is None, runs until KeyboardInterrupt. Returns a list of
    {topic, payload, ts, retain} dicts collected during the watch; `retain`
    is True for the messages a broker delivers immediately at subscribe time
    to serve its last retained value for a topic.
    """
    collected: list[dict] = []

    def _on_msg(_client, _ud, msg):
        rec = {
            "topic": msg.topic,
            "payload": msg.payload.decode("utf-8", errors="replace"),
            "ts": time.time(),
            # True for a broker-reserved message delivered at subscribe time.
            # `mqtt get` / `mqtt settings` read exactly these, so a value can
            # be *read back*, not just watched flowing past.
            "retain": bool(getattr(msg, "retain", False)),
        }
        collected.append(rec)
        if callback:
            try:
                callback(rec["topic"], rec["payload"])
            except Exception:
                # A caller-supplied callback must never abort message collection,
                # but silently discarding its exception (the previous
                # `except Exception: pass`) made a broken callback impossible to
                # diagnose - subscribe() just returned fewer results than
                # expected with no indication why. Log and carry on instead.
                logging.getLogger(__name__).warning(
                    "mqtt subscribe callback failed for topic %s", rec["topic"], exc_info=True
                )

    c = _client(
        host,
        port,
        username=username,
        password=password,
        client_id=f"cli-anything-espresense-watch-{int(time.time())}",
    )
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


def publish_device_config(
    host: str,
    device_id: str,
    config: dict,
    *,
    port: int = 1883,
    username: Optional[str] = None,
    password: Optional[str] = None,
    prefix: str = "espresense",
    retain: bool = True,
) -> dict:
    """Publish a per-device fingerprint config to `<prefix>/settings/<id>/config`.

    This is the second half of the settings topic family documented at the top
    of this module — `publish_setting` covers `rooms/<id>/<key>/set` (what a
    NODE should do) and this covers `settings/<id>/config` (what the fleet
    should believe about a tracked DEVICE: its name, its rssi@1m, its alias).

    Retained by default, so nodes that are offline right now still pick the
    config up when they reconnect.
    """
    if not device_id or "/" in device_id:
        raise MqttError(f"device_id must be non-empty and contain no '/': {device_id!r}")
    if not isinstance(config, dict):
        raise MqttError(f"config must be a mapping, got {type(config).__name__}")
    topic = f"{prefix}/settings/{device_id}/config"
    return publish_raw(
        host,
        topic,
        config,
        port=port,
        username=username,
        password=password,
        retain=retain,
    )


# ──────────────────────────────────────────────────────── read side
#
# The writing half of this module (publish_setting / publish_global_setting /
# publish_device_config) puts state on the broker as *retained* messages —
# that is how nodes and the companion re-apply settings at startup. The
# helpers below read that state back, so a `set-global` can be verified
# without the companion REST API being up (the exact scenario the MQTT
# route exists for). Like `telemetry.aggregate_*`, the aggregation is pure:
# it folds `watch()` records and does no I/O.


def parse_payload(raw: str):
    """Decode an MQTT payload: JSON where it parses, the raw string otherwise.

    Global settings payloads are frequently JSON (`gps` is an object) but
    scalars arrive as bare text (`telemetry` is `true`, `expiration` is
    `300`), and this must never raise on the text a firmware decided to
    send — worst case the payload stays a string, which is still honest.
    """
    raw_str = str(raw)
    if raw_str.strip() in ("", "null"):
        return None
    try:
        return json.loads(raw_str)
    except (json.JSONDecodeError, ValueError):
        return raw_str


def retained(records: list[dict]) -> list[dict]:
    """Filter `watch()` records down to the broker-retained ones.

    Live messages and retained read-backs arrive on the same topics during
    the collection window; only retained messages are the broker's current
    state, so anything else is in-flight traffic someone just published and
    must not be mistaken for a setting that survived a restart.
    """
    return [r for r in (records or []) if isinstance(r, dict) and r.get("retain")]


def latest_by_topic(records: list[dict]) -> dict:
    """Keep only the most recent record per topic (tie broken by retain).

    Reading back one topic can see several messages if a publisher is
    active; the retained value wins, then recency, so what comes out is the
    value the broker will serve to the next subscriber.
    """
    best: dict = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        topic = rec.get("topic")
        if topic is None:
            continue
        if topic not in best or _beats(rec, best[topic]):
            best[topic] = rec
    return best


def _beats(candidate: dict, incumbent: dict) -> bool:
    """Does `candidate` outrank the `incumbent` record for the same topic?"""
    retain_a = bool(candidate.get("retain"))
    retain_b = bool(incumbent.get("retain"))
    if retain_a != retain_b:
        return retain_a
    return (candidate.get("ts") or 0) >= (incumbent.get("ts") or 0)


def settings_view(records: list[dict]) -> dict:
    """Fold global-setting records into {key: parsed value}.

    Input is `watch()` records from a `<prefix>/settings/+/set` subscription
    (pass them through `retained()` first for read-back semantics). The key
    is the `*` segment of the topic, so a record only counts when the topic
    literally reads `<...>/settings/<key>/set` — per-device config records
    (`<prefix>/settings/<id>/config`) and per-node writes
    (`<prefix>/rooms/<id>/<key>/set`) never misattribute a key, whatever
    collection they are folded with. Values go through `parse_payload` —
    JSON where it parses.
    """
    view: dict = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        topic = str(rec.get("topic") or "")
        parts = topic.split("/")
        if len(parts) < 3 or parts[-1] != "set" or parts[-3] != "settings":
            continue
        view[parts[-2]] = parse_payload(rec.get("payload", ""))
    return view
