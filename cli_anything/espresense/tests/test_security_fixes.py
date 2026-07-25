"""Regression tests for security-finding fixes (B105, B110).

Each test pins the behaviour that the corresponding fix preserves so a future
regression (re-introducing the flagged pattern) is caught.
"""

from __future__ import annotations

import logging

import pytest

from cli_anything.espresense.core import project
from cli_anything.espresense.core import rooms as rooms_core
from cli_anything.espresense.core import stream
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
        points: [[0,0],[1,0],[1,1],[0,1]]
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
    room: "Sophie Bedroom "
  - name: spare-room
    point: [2.5, 0.5, 1.0]
    floors: ["first"]
    room: "Spare Room "
  - name: bedroom
    point: [3.5, 0.5, 1.0]
    floors: ["first"]
    room: "Master Bedroom "
"""


@pytest.fixture
def parsed():
    return yaml_io.load(SAMPLE)



# ---------------------------------------------------------------------------
# B105 — project.DEFAULTS["mqtt_password"] must be None (no hardcoded secret)
# ---------------------------------------------------------------------------
def test_defaults_mqtt_password_is_none():
    """The mqtt_password default must remain None — it is a config *key*,
    not a hardcoded credential. A non-None literal here would re-trigger B105
    and would ship a secret in source."""
    assert project.DEFAULTS["mqtt_password"] is None


def test_defaults_has_mqtt_password_key():
    """The key must still exist so load_config() round-trips profiles that
    omit it (preserved behaviour)."""
    assert "mqtt_password" in project.DEFAULTS


# ---------------------------------------------------------------------------
# B110 — stream callback errors must not crash the stream (now logged, not pass)
# ---------------------------------------------------------------------------
class _FakeTimeout(Exception):
    """Stands in for websocket.WebSocketTimeoutException."""


class _FakeWS:
    """Minimal websocket stand-in: yields the given events, then ends the stream.

    Raising KeyboardInterrupt once the events are exhausted (stream() catches it)
    makes the loop terminate on DATA rather than on the wall clock. Previously
    recv() returned "" forever and the tests relied on a 1 ms duration expiring
    before/after N events — pure scheduling luck: it passed on the build host and
    failed in CI with `assert len(collected) == 2` getting 1.
    """

    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def settimeout(self, _t):
        pass

    def recv(self):
        if self._events:
            return self._events.pop(0)
        raise KeyboardInterrupt  # deterministic end-of-stream

    def close(self):
        self.closed = True


def test_stream_callback_exception_does_not_crash(monkeypatch, caplog):
    """A user callback that raises must not abort the stream. Previously the
    exception was silently swallowed (B110); now it is logged but the stream
    still returns the collected events."""
    events = [
        '{"type": "deviceChanged", "id": "abc"}',
        '{"type": "deviceChanged", "id": "def"}',
    ]
    fake = _FakeWS(events)

    monkeypatch.setattr(stream, "websocket", type("W", (), {"create_connection": staticmethod(lambda *_a, **_k: fake),
                                    # stream() references this in an except clause; a stub
                                    # without it raises AttributeError the moment recv()
                                    # actually throws.
                                    "WebSocketTimeoutException": _FakeTimeout}))

    def bad_callback(_event):
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="cli_anything.espresense.core.stream"):
        collected = stream.stream("http://x", duration=30, callback=bad_callback)

    # Both events collected despite callback raising on each
    assert len(collected) == 2
    # The exception is now surfaced in the log instead of being silently passed
    assert any("stream callback raised" in r.message for r in caplog.records)


def test_stream_ws_close_failure_does_not_raise(monkeypatch, caplog):
    """If ws.close() itself raises during cleanup, stream() must still return
    the collected events (preserved behaviour). Previously the error was
    silently swallowed (B110); now it is logged at debug level."""
    events = ['{"type": "nodeStateChanged"}']

    class CloseFailsWS(_FakeWS):
        def close(self):
            raise OSError("close failed")

    fake = CloseFailsWS(events)

    monkeypatch.setattr(stream, "websocket", type("W", (), {"create_connection": staticmethod(lambda *_a, **_k: fake),
                                    # stream() references this in an except clause; a stub
                                    # without it raises AttributeError the moment recv()
                                    # actually throws.
                                    "WebSocketTimeoutException": _FakeTimeout}))

    with caplog.at_level(logging.DEBUG, logger="cli_anything.espresense.core.stream"):
        collected = stream.stream("http://x", duration=30)

    assert len(collected) == 1
    # close error surfaced in debug log instead of silent pass
    assert any("ws.close() failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# B101 — assert_used in test_core.py must not be optimized away
# ---------------------------------------------------------------------------

def test_regression_rename_updates_kitchen_node_room_ref(parsed):
    """Regression for former assert kitchen_node["room"] == "Cook Room".

    The rename() function must update the node's room reference to match
    the renamed room. Previously used bare assert; now uses pytest.fail().
    """
    from cli_anything.espresense.core import rooms as rooms_core
    rooms_core.rename(parsed, "Kitchen", "Cook Room")
    kitchen_node = next(n for n in parsed["nodes"] if n["name"] == "kitchen")
    if kitchen_node["room"] != "Cook Room":
        pytest.fail(f"Expected kitchen node room == 'Cook Room', got {kitchen_node['room']}")


def test_regression_rename_strips_whitespace_and_reassigns_node(parsed):
    """Regression for former assert noah["room"] == "Sophie Bedroom NEW".

    The noah-bedroom node had room: "Sophie Bedroom " (trailing space).
    After renaming Sophie Bedroom to "Sophie Bedroom NEW", the node's
    room reference must be updated AND whitespace must be stripped.
    Previously used bare assert; now uses pytest.fail().
    """
    from cli_anything.espresense.core import rooms as rooms_core
    rooms_core.rename(parsed, "Sophie Bedroom", "Sophie Bedroom NEW")
    noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
    if noah["room"] != "Sophie Bedroom NEW":
        pytest.fail(f"Expected noah node room == 'Sophie Bedroom NEW', got {noah['room']}")


def test_regression_rename_strips_whitespace_without_renaming(parsed):
    """Regression for former assert bedroom["room"] == "Master Bedroom".

    The bedroom node had room: "Master Bedroom " (trailing space).
    After renaming Sophie Bedroom (not Master Bedroom), the bedroom node's
    room reference must still have whitespace stripped but NOT renamed.
    Previously used bare assert; now uses pytest.fail().
    """
    from cli_anything.espresense.core import rooms as rooms_core
    rooms_core.rename(parsed, "Sophie Bedroom", "Sophie Bedroom NEW")
    bedroom = next(n for n in parsed["nodes"] if n["name"] == "bedroom")
    if bedroom["room"] != "Master Bedroom":
        pytest.fail(f"Expected bedroom node room == 'Master Bedroom', got {bedroom['room']}")
