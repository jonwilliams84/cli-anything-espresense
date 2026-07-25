"""Regression tests for security-finding fixes (B105, B110).

Each test pins the behaviour that the corresponding fix preserves so a future
regression (re-introducing the flagged pattern) is caught.
"""

from __future__ import annotations

import logging

import pytest

from cli_anything.espresense.core import project
from cli_anything.espresense.core import stream


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
class _FakeWS:
    """Minimal websocket stand-in: yields one event then closes."""

    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def settimeout(self, _t):
        pass

    def recv(self):
        if self._events:
            return self._events.pop(0)
        return ""  # empty -> stream loop continues / ends

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

    monkeypatch.setattr(stream, "websocket", type("W", (), {"create_connection": staticmethod(lambda *_a, **_k: fake)}))

    def bad_callback(_event):
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="cli_anything.espresense.core.stream"):
        collected = stream.stream("http://x", duration=0.001, callback=bad_callback)

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

    monkeypatch.setattr(stream, "websocket", type("W", (), {"create_connection": staticmethod(lambda *_a, **_k: fake)}))

    with caplog.at_level(logging.DEBUG, logger="cli_anything.espresense.core.stream"):
        collected = stream.stream("http://x", duration=0.001)

    assert len(collected) == 1
    # close error surfaced in debug log instead of silent pass
    assert any("ws.close() failed" in r.message for r in caplog.records)
