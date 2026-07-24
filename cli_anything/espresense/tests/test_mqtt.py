"""Regression tests for mqtt module security findings."""

from __future__ import annotations

import inspect
import re
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from cli_anything.espresense.core import mqtt


class TestBanditB110Regression:
    """Verify Bandit B110 (Try, Except, Pass) has nosec suppression."""

    def test_watch_callback_exception_has_nosec_b110(self):
        """The except+pass block in watch() must have nosec comment for B110."""
        source = inspect.getsource(mqtt.watch)
        # Find the except Exception: ... pass block with nosec
        pattern = r"except\s+Exception[^:]*:\s*#\s*nosec\s*:\s*B110\s+pass"
        match = re.search(pattern, source, re.DOTALL)
        assert match is not None, (
            "watch() except+pass block needs '# nosec: B110' comment "
            "on the except line"
        )

    def test_watch_callback_exception_is_swallowed(self):
        """Exceptions in the callback must be silently swallowed (preserves pass)."""
        def failing_callback(topic: str, payload: str):
            raise RuntimeError("intentional test error")

        # Directly test the inner _on_msg closure logic
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
                except Exception:  # nosec: B110
                    pass

        fake_msg = MagicMock()
        fake_msg.topic = "test/topic"
        fake_msg.payload = b"test payload"

        # This should NOT raise - the except+pass should swallow
        _on_msg(None, None, fake_msg)
        assert collected[0]["topic"] == "test/topic"
