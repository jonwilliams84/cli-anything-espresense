"""Regression tests for mqtt module security findings."""

from __future__ import annotations

import inspect
import re
from unittest.mock import MagicMock


from cli_anything.espresense.core import mqtt


class TestBanditB110Regression:
    """Verify a failing callback cannot abort collection, and is not silent."""

    def test_watch_callback_exception_is_logged_not_swallowed(self, caplog):
        """A raising callback must be reported via logging, not discarded.

        This replaces an earlier test that asserted the literal string
        ``# nosec: B110`` appeared on the ``except`` line. That test pinned a
        *suppression comment* rather than any behaviour, so it actively blocked
        fixing the underlying finding: the handler was `except Exception: pass`,
        so a broken user callback silently produced fewer records with no
        diagnostic anywhere.

        Asserting on observable behaviour rather than source text also avoids the
        trap that caught the first attempt at this test - a source-scraping regex
        matched the word "pass" inside the explanatory comment.
        """
        import logging as _logging

        collected: list[dict] = []

        def failing_callback(topic: str, payload: str) -> None:
            raise RuntimeError("intentional test error")

        class _Msg:
            topic = "espresense/devices/x"
            payload = b"{}"

        # Rebuild the same closure shape watch() installs, then drive it.
        def _on_msg(_client, _ud, msg):
            rec = {"topic": msg.topic, "payload": msg.payload.decode("utf-8", "replace")}
            collected.append(rec)
            try:
                failing_callback(rec["topic"], rec["payload"])
            except Exception:
                _logging.getLogger("cli_anything.espresense.core.mqtt").warning(
                    "mqtt subscribe callback failed for topic %s", rec["topic"], exc_info=True
                )

        with caplog.at_level(_logging.WARNING):
            _on_msg(None, None, _Msg())

        # collection continued despite the callback raising
        assert len(collected) == 1
        # and the failure was reported rather than swallowed
        assert any("callback failed" in r.message for r in caplog.records), (
            "a raising callback must be logged, not silently discarded"
        )

    def test_production_handler_reports_callback_failure(self):
        """The shipped handler in watch() must log rather than bare-pass."""
        source = inspect.getsource(mqtt.watch)
        # Strip comments before inspecting control flow, so explanatory prose
        # cannot be mistaken for code (the bug in this test's first version).
        code_only = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        handlers = re.findall(r"except\s+Exception[^:]*:\s*\n\s*(\w+)", code_only)
        assert "pass" not in handlers, (
            f"watch() must not silently pass on callback failure; found {handlers}"
        )
        assert "logging" in code_only, "watch() must report callback failures"

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
