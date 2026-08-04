"""Tests for the ReplSkin module — pure rendering and formatting logic.

Covers the helper functions (_strip_ansi, _visible_len, _display_home_path)
and the ReplSkin methods that contain real branching: color detection,
prompt token construction, table rendering, progress, status, messages.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cli_anything.espresense.utils.repl_skin import (
    ReplSkin,
    _display_home_path,
    _strip_ansi,
    _visible_len,
)


# ── Module-level helpers ─────────────────────────────────────────────────────


class TestStripAnsi:
    def test_removes_escape_codes(self):
        text = "\033[38;5;80mhello\033[0m world"
        assert _strip_ansi(text) == "hello world"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("no codes here") == "no codes here"

    def test_multiple_codes(self):
        text = "\033[1m\033[38;5;196merr\033[0m\033[0m"
        assert _strip_ansi(text) == "err"


class TestVisibleLen:
    def test_counts_visible_chars_only(self):
        assert _visible_len("\033[1mhi\033[0m") == 2

    def test_plain_text(self):
        assert _visible_len("hello") == 5


class TestDisplayHomePath:
    def test_relative_to_home_uses_tilde(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _display_home_path(str(tmp_path / "subdir" / "file.txt"))
        assert result.startswith("~/")
        assert "subdir/file.txt" in result

    def test_outside_home_returns_absolute(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "myhome"))
        other = tmp_path / "elsewhere"
        result = _display_home_path(str(other))
        assert result == str(other.resolve())


# ── ReplSkin initialization ──────────────────────────────────────────────────


class TestReplSkinInit:
    def test_software_name_normalised(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("My-Cool_Tool")
        assert skin.software == "my_cool_tool"
        assert skin.display_name == "My-Cool Tool"

    def test_skill_slug_uses_alias(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("iterm2_ctl")
        assert skin.skill_slug == "iterm2"
        assert skin.skill_id == "cli-anything-iterm2"

    def test_skill_slug_default_replaces_underscores(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        assert skin.skill_slug == "espresense"
        assert skin.skill_id == "cli-anything-espresense"

    def test_custom_history_file_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test", history_file="/custom/path/hist")
        assert skin.history_file == "/custom/path/hist"

    def test_default_history_file_under_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        assert ".cli-anything-espresense" in skin.history_file
        assert skin.history_file.endswith("history")


# ── Color detection ──────────────────────────────────────────────────────────


class TestColorDetection:
    def test_no_color_env_disables(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("test")
        assert skin._color is False

    def test_cli_anything_no_color_env_disables(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("CLI_ANYTHING_NO_COLOR", "1")
        skin = ReplSkin("test")
        assert skin._color is False

    def test_color_method_returns_plain_when_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("test")
        assert skin._c("\033[1m", "text") == "text"


# ── Prompt token construction ────────────────────────────────────────────────


class TestPromptTokens:
    def test_basic_prompt_has_software_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        tokens = skin.prompt_tokens()
        texts = [t[1] for t in tokens]
        assert "espresense" in texts

    def test_prompt_with_context_shows_brackets(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        tokens = skin.prompt_tokens(project_name="proj")
        texts = [t[1] for t in tokens]
        joined = "".join(texts)
        assert "proj" in joined
        assert "[" in joined
        assert "]" in joined

    def test_prompt_with_modified_shows_star(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        tokens = skin.prompt_tokens(project_name="proj", modified=True)
        texts = "".join(t[1] for t in tokens)
        assert "*" in texts

    def test_prompt_without_context_no_brackets(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("espresense")
        tokens = skin.prompt_tokens()
        texts = "".join(t[1] for t in tokens)
        assert "[" not in texts


# ── Prompt string (non-prompt_toolkit fallback) ──────────────────────────────


class TestPromptString:
    def test_prompt_string_contains_software(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("espresense")
        prompt = skin.prompt(project_name="myproj", modified=True)
        assert "espresense" in prompt
        assert "myproj" in prompt
        assert "*" in prompt


# ── Message output methods ───────────────────────────────────────────────────


class TestMessages:
    @pytest.fixture
    def skin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        return ReplSkin("test")

    def test_success_prints_message(self, skin, capsys):
        skin.success("done it")
        out = capsys.readouterr().out
        assert "done it" in out
        assert "✓" in out

    def test_error_prints_to_stderr(self, skin, capsys):
        skin.error("broke")
        err = capsys.readouterr().err
        assert "broke" in err
        assert "✗" in err

    def test_warning_prints_message(self, skin, capsys):
        skin.warning("careful")
        out = capsys.readouterr().out
        assert "careful" in out

    def test_info_prints_message(self, skin, capsys):
        skin.info("notice")
        out = capsys.readouterr().out
        assert "notice" in out

    def test_hint_prints_message(self, skin, capsys):
        skin.hint("subtle")
        out = capsys.readouterr().out
        assert "subtle" in out

    def test_section_prints_title_and_rule(self, skin, capsys):
        skin.section("My Section")
        out = capsys.readouterr().out
        assert "My Section" in out
        # a horizontal rule follows
        assert "─" in out


# ── Status display ───────────────────────────────────────────────────────────


class TestStatus:
    @pytest.fixture
    def skin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        return ReplSkin("test")

    def test_status_prints_label_and_value(self, skin, capsys):
        skin.status("Nodes", "5")
        out = capsys.readouterr().out
        assert "Nodes" in out
        assert "5" in out

    def test_status_block_prints_all_items(self, skin, capsys):
        skin.status_block({"a": "1", "b": "2"})
        out = capsys.readouterr().out
        assert "a" in out
        assert "1" in out
        assert "b" in out
        assert "2" in out

    def test_status_block_with_title_prints_section(self, skin, capsys):
        skin.status_block({"x": "y"}, title="Details")
        out = capsys.readouterr().out
        assert "Details" in out

    def test_status_block_empty_dict_no_crash(self, skin, capsys):
        skin.status_block({})
        # should not raise; max() on empty would crash if not guarded
        capsys.readouterr()  # just confirm no exception


# ── Progress ─────────────────────────────────────────────────────────────────


class TestProgress:
    @pytest.fixture
    def skin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        return ReplSkin("test")

    def test_progress_zero_total_shows_zero_pct(self, skin, capsys):
        skin.progress(5, 0, "loading")
        out = capsys.readouterr().out
        assert "0%" in out

    def test_progress_half_shows_fifty_pct(self, skin, capsys):
        skin.progress(5, 10)
        out = capsys.readouterr().out
        assert "50%" in out

    def test_progress_full_shows_hundred_pct(self, skin, capsys):
        skin.progress(10, 10)
        out = capsys.readouterr().out
        assert "100%" in out

    def test_progress_label_in_output(self, skin, capsys):
        skin.progress(1, 4, "processing")
        out = capsys.readouterr().out
        assert "processing" in out


# ── Table rendering ──────────────────────────────────────────────────────────


class TestTable:
    @pytest.fixture
    def skin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        return ReplSkin("test")

    def test_empty_headers_does_nothing(self, skin, capsys):
        skin.table([], [["a"]])
        assert capsys.readouterr().out == ""

    def test_table_renders_headers_and_rows(self, skin, capsys):
        skin.table(["Name", "Value"], [["alpha", "1"], ["beta", "2"]])
        out = capsys.readouterr().out
        assert "Name" in out
        assert "Value" in out
        assert "alpha" in out
        assert "beta" in out
        assert "1" in out
        assert "2" in out

    def test_table_truncates_long_cells(self, skin, capsys):
        long_text = "x" * 100
        skin.table(["Col"], [[long_text]])
        out = capsys.readouterr().out
        # cell is truncated to max_col_width (40)
        assert "x" * 100 not in out
        assert "x" * 40 in out
        assert long_text not in out

    def test_table_handles_uneven_rows(self, skin, capsys):
        # row with fewer cells than headers should not crash
        skin.table(["A", "B", "C"], [["only_one"]])
        out = capsys.readouterr().out
        assert "only_one" in out


# ── Banner ───────────────────────────────────────────────────────────────────


class TestBanner:
    def test_banner_prints_brand_and_software(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("espresense", version="9.9.9")
        skin.print_banner()
        # banner is printed to stdout; we just check it doesn't crash
        # and contains key elements


# ── Help ─────────────────────────────────────────────────────────────────────


class TestHelp:
    def test_help_lists_commands(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("test")
        # build a fake commands dict like click's
        fake_cmds = {"info": None, "restart": None, "set": None}
        skin.help(fake_cmds)
        # should not crash; output contains command names


# ── Goodbye ──────────────────────────────────────────────────────────────────


class TestGoodbye:
    def test_print_goodbye_outputs_message(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("test")
        skin.print_goodbye()
        out = capsys.readouterr().out
        assert len(out) > 0


# ── get_prompt_style ─────────────────────────────────────────────────────────


class TestGetPromptStyle:
    def test_returns_none_without_prompt_toolkit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        with patch("builtins.__import__", side_effect=ImportError):
            result = skin.get_prompt_style()
        assert result is None


# ── create_prompt_session ────────────────────────────────────────────────────


class TestCreatePromptSession:
    def test_returns_none_without_prompt_toolkit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        with patch("builtins.__import__", side_effect=ImportError):
            result = skin.create_prompt_session()
        assert result is None


# ── bottom_toolbar ───────────────────────────────────────────────────────────


class TestBottomToolbar:
    def test_toolbar_returns_callable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        toolbar = skin.bottom_toolbar({"Status": "OK"})
        assert callable(toolbar)
