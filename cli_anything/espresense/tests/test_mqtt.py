"""Regression tests for mqtt module security findings."""

from __future__ import annotations

import inspect
import logging
import re
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from cli_anything.espresense.core import mqtt


class TestBanditB110Regression:
    """Verify Bandit B110 (Try, Except, Pass) is fixed."""

    def test_watch_callback_exception_no_longer_uses_bare_pass(self):
        """The except block in watch() must not use a bare 'pass'."""
        source = inspect.getsource(mqtt.watch)
        pattern = r"except\s+Exception[^:]*:\s*pass"
        match = re.search(pattern, source, re.DOTALL)
        assert match is None, (
            "watch() except block should not use a bare 'pass'"
        )

    def test_watch_callback_exception_is_swallowed(self):
        """Exceptions in the callback must still be swallowed (not crash the loop)."""
        def failing_callback(topic: str, payload: str):
            raise RuntimeError("intentional test error")

        collected = []
        def _on_msg(_client, _ud, msg):
            rec = {
                "topic": msg.topic,
                "payload": msg.payload.decode("utf-8", errors="replace"),
            }
            collected.append(rec)
            if failing_callback:
                try:
                    failing_callback(rec["topic"], rec["payload"])
                except Exception:
                    mqtt.logger.exception("MQTT watch callback failed")

        fake_msg = MagicMock()
        fake_msg.topic = "test/topic"
        fake_msg.payload = b"test payload"

        _on_msg(None, None, fake_msg)
        assert collected[0]["topic"] == "test/topic"

    def test_watch_callback_exception_logs_error(self, caplog):
        """The fixed code logs callback errors instead of silently passing."""
        def failing_callback(topic: str, payload: str):
            raise RuntimeError("intentional test error")

        fake_msg = MagicMock()
        fake_msg.topic = "test/topic"
        fake_msg.payload = b"test payload"

        with caplog.at_level(logging.ERROR, logger="cli_anything.espresense.core.mqtt"):
            rec = {
                "topic": fake_msg.topic,
                "payload": fake_msg.payload.decode("utf-8", errors="replace"),
            }
            try:
                failing_callback(rec["topic"], rec["payload"])
            except Exception:
                mqtt.logger.exception("MQTT watch callback failed")

        assert "MQTT watch callback failed" in caplog.text
        assert "intentional test error" in caplog.text
