"""Unit tests for `core/settings.py` — dotted-path tuning edits.

The module is schema-free by design, so the tests pin *behaviour* (coercion,
redaction, guard rails, mapping creation) rather than any particular
companion release's key names.
"""

from __future__ import annotations

import pytest

from cli_anything.espresense.core import settings
from cli_anything.espresense.utils import yaml_io

TUNING_YAML = """\
timeout: 30
away_timeout: 120
mqtt:
  host: broker.local
  port: 1883
  username: espresense
  password: hunter2
gps:
  latitude: 51.5
  longitude: -0.1
locators:
  nadaraya_watson:
    enabled: true
    bandwidth: 0.7
  nelder_mead:
    enabled: false
  nearest_node:
    max_distance: 3
optimizers:
  absorption:
    enabled: true
  rx_adj_rssi: true
floors:
  - id: gf
    rooms: []
nodes:
  - name: n1
devices:
  - id: d1
"""


@pytest.fixture()
def cfg():
    return yaml_io.load(TUNING_YAML)


class TestCoerce:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("no", False),
            ("off", False),
            ("null", None),
            ("none", None),
            ("~", None),
            ("30", 30),
            ("-65", -65),
            ("0.7", 0.7),
            ("-0.1", -0.1),
            ("broker.local", "broker.local"),
        ],
    )
    def test_auto_reads_the_obvious_type(self, text, expected):
        assert settings.coerce(text) == expected

    def test_auto_parses_json_lists(self):
        assert settings.coerce("[1, 2]") == [1, 2]

    def test_auto_parses_json_objects(self):
        assert settings.coerce('{"a": 1}') == {"a": 1}

    def test_broken_json_falls_back_to_the_raw_string(self):
        assert settings.coerce("[1, ") == "[1, "

    def test_explicit_str_keeps_a_bool_looking_value(self):
        assert settings.coerce("true", "str") == "true"

    def test_explicit_str_keeps_a_leading_zero_port(self):
        assert settings.coerce("0883", "str") == "0883"

    def test_int_kind(self):
        assert settings.coerce("42", "int") == 42

    def test_int_kind_rejects_a_float(self):
        with pytest.raises(settings.SettingsError):
            settings.coerce("4.2", "int")

    def test_float_kind(self):
        assert settings.coerce("4.2", "float") == 4.2

    def test_float_kind_rejects_words(self):
        with pytest.raises(settings.SettingsError):
            settings.coerce("wide", "float")

    @pytest.mark.parametrize("text", ["on", "1", "yes", "true"])
    def test_bool_kind_truthy(self, text):
        assert settings.coerce(text, "bool") is True

    @pytest.mark.parametrize("text", ["off", "0", "no", "false"])
    def test_bool_kind_falsy(self, text):
        assert settings.coerce(text, "bool") is False

    def test_bool_kind_rejects_nonsense(self):
        with pytest.raises(settings.SettingsError):
            settings.coerce("maybe", "bool")

    def test_json_kind(self):
        assert settings.coerce('{"a": [1]}', "json") == {"a": [1]}

    def test_json_kind_reports_the_parse_error(self):
        with pytest.raises(settings.SettingsError, match="invalid JSON"):
            settings.coerce("{oops", "json")

    def test_unknown_kind_is_refused(self):
        with pytest.raises(settings.SettingsError, match="unknown value type"):
            settings.coerce("x", "yaml")

    def test_non_string_input_is_stringified(self):
        assert settings.coerce(42) == 42


class TestRedaction:
    @pytest.mark.parametrize("key", ["password", "PASSWORD", "mqtt_password", "api_key", "token"])
    def test_secret_keys_are_recognised(self, key):
        assert settings.is_secret(key) is True

    @pytest.mark.parametrize("key", ["host", "username", "bandwidth", "keys"])
    def test_ordinary_keys_are_not(self, key):
        assert settings.is_secret(key) is False

    def test_nested_secrets_are_masked(self, cfg):
        assert settings.redact(dict(cfg))["mqtt"]["password"] == settings.REDACTED

    def test_neighbouring_values_survive(self, cfg):
        assert settings.redact(dict(cfg))["mqtt"]["username"] == "espresense"

    def test_a_null_secret_stays_null(self):
        assert settings.redact({"password": None}) == {"password": None}

    def test_secrets_inside_lists_are_masked(self):
        assert settings.redact([{"token": "t"}]) == [{"token": settings.REDACTED}]

    def test_scalars_pass_through(self):
        assert settings.redact(7) == 7


class TestSplitPath:
    def test_splits_on_dots(self):
        assert settings.split_path("a.b.c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert settings.split_path(" a . b ") == ["a", "b"]

    @pytest.mark.parametrize("path", ["", "   ", None])
    def test_empty_path_is_refused(self, path):
        with pytest.raises(settings.SettingsError, match="non-empty"):
            settings.split_path(path)

    @pytest.mark.parametrize("path", ["a..b", ".a", "a."])
    def test_empty_segments_are_refused(self, path):
        with pytest.raises(settings.SettingsError, match="empty segment"):
            settings.split_path(path)


class TestGetPath:
    def test_reads_a_scalar(self, cfg):
        assert settings.get_path(cfg, "timeout")["value"] == 30

    def test_reads_a_nested_value(self, cfg):
        assert settings.get_path(cfg, "locators.nadaraya_watson.bandwidth")["value"] == 0.7

    def test_reads_a_whole_mapping(self, cfg):
        assert settings.get_path(cfg, "gps")["value"]["latitude"] == 51.5

    def test_missing_path_is_reported_not_raised(self, cfg):
        out = settings.get_path(cfg, "locators.ghost.enabled")
        assert out == {
            "path": "locators.ghost.enabled",
            "found": False,
            "value": None,
            "secret": False,
        }

    def test_descending_into_a_scalar_is_not_found(self, cfg):
        assert settings.get_path(cfg, "timeout.nope")["found"] is False

    def test_secret_is_redacted_by_default(self, cfg):
        out = settings.get_path(cfg, "mqtt.password")
        assert out["value"] == settings.REDACTED
        assert out["secret"] is True

    def test_reveal_returns_the_real_value(self, cfg):
        assert settings.get_path(cfg, "mqtt.password", reveal=True)["value"] == "hunter2"

    def test_a_mapping_containing_a_secret_is_redacted(self, cfg):
        assert settings.get_path(cfg, "mqtt")["value"]["password"] == settings.REDACTED


class TestSetPath:
    def test_overwrites_a_scalar(self, cfg):
        out = settings.set_path(cfg, "away_timeout", "300")
        assert (out["before"], out["after"]) == (120, 300)
        assert cfg["away_timeout"] == 300

    def test_writes_a_nested_bool(self, cfg):
        settings.set_path(cfg, "locators.nelder_mead.enabled", "true")
        assert cfg["locators"]["nelder_mead"]["enabled"] is True

    def test_creates_missing_parents_and_reports_them(self, cfg):
        out = settings.set_path(cfg, "locators.gauss_newton.enabled", "true")
        assert out["created"] == ["locators.gauss_newton"]
        assert cfg["locators"]["gauss_newton"] == {"enabled": True}

    def test_creates_a_whole_new_section(self, cfg):
        out = settings.set_path(cfg, "history.expire_after.days", "7")
        assert out["created"] == ["history", "history.expire_after"]

    def test_existing_parents_are_not_reported_as_created(self, cfg):
        assert settings.set_path(cfg, "mqtt.port", "8883")["created"] == []

    def test_type_override_is_honoured(self, cfg):
        settings.set_path(cfg, "mqtt.host", "true", kind="str")
        assert cfg["mqtt"]["host"] == "true"

    def test_secret_values_are_masked_in_the_result(self, cfg):
        out = settings.set_path(cfg, "mqtt.password", "s3cret")
        assert out["before"] == settings.REDACTED
        assert out["after"] == settings.REDACTED
        assert cfg["mqtt"]["password"] == "s3cret"

    def test_descending_into_a_scalar_is_refused(self, cfg):
        with pytest.raises(settings.SettingsError, match="cannot descend"):
            settings.set_path(cfg, "timeout.value", "5")

    @pytest.mark.parametrize(
        "path", ["nodes.0.room", "floors.0.id", "devices.0.name", "rooms.0.points"]
    )
    def test_structural_blocks_are_refused(self, cfg, path):
        with pytest.raises(settings.SettingsError, match="commands"):
            settings.set_path(cfg, path, "x")

    def test_the_refusal_names_the_right_command(self, cfg):
        with pytest.raises(settings.SettingsError, match="`nodes`"):
            settings.set_path(cfg, "nodes.0.room", "x")

    def test_a_top_level_key_named_like_a_block_is_still_settable(self, cfg):
        # `devices` alone (no sub-path) is not an attempt to edit an entry
        settings.set_path(cfg, "device_trackers", "[]")
        assert cfg["device_trackers"] == []

    def test_non_mapping_root_is_refused(self):
        with pytest.raises(settings.SettingsError, match="not a YAML mapping"):
            settings.set_path([], "a", "1")

    def test_the_document_round_trips(self, cfg):
        settings.set_path(cfg, "timeout", "45")
        assert "timeout: 45" in yaml_io.dumps(cfg)


class TestUnsetPath:
    def test_removes_a_scalar(self, cfg):
        out = settings.unset_path(cfg, "away_timeout")
        assert (out["removed"], out["before"]) == (True, 120)
        assert "away_timeout" not in cfg

    def test_removes_a_nested_key(self, cfg):
        settings.unset_path(cfg, "locators.nearest_node.max_distance")
        assert cfg["locators"]["nearest_node"] == {}

    def test_missing_key_reports_false(self, cfg):
        assert settings.unset_path(cfg, "ghost")["removed"] is False

    def test_missing_parent_reports_false(self, cfg):
        assert settings.unset_path(cfg, "ghost.child")["removed"] is False

    def test_secret_value_is_masked_in_the_result(self, cfg):
        assert settings.unset_path(cfg, "mqtt.password")["before"] == settings.REDACTED

    def test_removed_mapping_is_redacted(self, cfg):
        assert settings.unset_path(cfg, "mqtt")["before"]["password"] == settings.REDACTED

    def test_structural_sub_path_is_refused(self, cfg):
        with pytest.raises(settings.SettingsError):
            settings.unset_path(cfg, "nodes.0")

    def test_deleting_a_whole_structural_block_is_refused(self, cfg):
        with pytest.raises(settings.SettingsError, match="refusing to delete"):
            settings.unset_path(cfg, "nodes")


class TestSummary:
    def test_structural_blocks_collapse_to_counts(self, cfg):
        out = settings.summary(cfg)
        assert out["nodes"] == "<1 entry>"
        assert out["floors"] == "<1 entry>"

    def test_plural_counts_read_correctly(self):
        parsed = yaml_io.load("nodes:\n  - name: a\n  - name: b\n")
        assert settings.summary(parsed)["nodes"] == "<2 entries>"

    def test_tuning_keys_are_shown_in_full(self, cfg):
        assert settings.summary(cfg)["locators"]["nadaraya_watson"]["bandwidth"] == 0.7

    def test_secrets_are_redacted(self, cfg):
        assert settings.summary(cfg)["mqtt"]["password"] == settings.REDACTED

    def test_reveal_shows_them(self, cfg):
        assert settings.summary(cfg, reveal=True)["mqtt"]["password"] == "hunter2"

    def test_section_filter(self, cfg):
        assert set(settings.summary(cfg, section="gps")) == {"gps"}

    def test_unknown_section_raises(self, cfg):
        with pytest.raises(KeyError):
            settings.summary(cfg, section="nope")

    def test_non_mapping_root_is_refused(self):
        with pytest.raises(settings.SettingsError):
            settings.summary("not a config")


class TestToggles:
    def test_lists_locators(self, cfg):
        rows = settings.list_toggles(cfg, "locators")
        assert [r["name"] for r in rows] == ["nadaraya_watson", "nelder_mead", "nearest_node"]

    def test_absent_enabled_defaults_to_on(self, cfg):
        rows = {r["name"]: r for r in settings.list_toggles(cfg, "locators")}
        assert rows["nearest_node"]["enabled"] is True

    def test_explicit_false_is_reported(self, cfg):
        rows = {r["name"]: r for r in settings.list_toggles(cfg, "locators")}
        assert rows["nelder_mead"]["enabled"] is False

    def test_params_exclude_the_enabled_flag(self, cfg):
        rows = {r["name"]: r for r in settings.list_toggles(cfg, "locators")}
        assert rows["nadaraya_watson"]["params"] == {"bandwidth": 0.7}

    def test_paramless_entries_report_none(self, cfg):
        rows = {r["name"]: r for r in settings.list_toggles(cfg, "locators")}
        assert rows["nelder_mead"]["params"] is None

    def test_scalar_form_is_tolerated(self, cfg):
        rows = {r["name"]: r for r in settings.list_toggles(cfg, "optimizers")}
        assert rows["rx_adj_rssi"]["enabled"] is True

    def test_absent_section_lists_nothing(self, cfg):
        assert settings.list_toggles(cfg, "weighting") == []

    def test_non_mapping_section_is_refused(self, cfg):
        with pytest.raises(settings.SettingsError, match="not a mapping"):
            settings.list_toggles(cfg, "nodes")

    def test_set_toggle_off(self, cfg):
        out = settings.set_toggle(cfg, "locators", "nadaraya_watson", False)
        assert (out["before"], out["after"]) == (True, False)
        assert cfg["locators"]["nadaraya_watson"]["enabled"] is False

    def test_set_toggle_on_adds_the_key(self, cfg):
        settings.set_toggle(cfg, "locators", "nearest_node", True)
        assert cfg["locators"]["nearest_node"]["enabled"] is True

    def test_set_toggle_keeps_other_params(self, cfg):
        settings.set_toggle(cfg, "locators", "nearest_node", False)
        assert cfg["locators"]["nearest_node"]["max_distance"] == 3

    def test_set_toggle_upgrades_a_scalar_entry(self, cfg):
        out = settings.set_toggle(cfg, "optimizers", "rx_adj_rssi", False)
        assert out["before"] is True
        assert cfg["optimizers"]["rx_adj_rssi"] == {"enabled": False}

    def test_unknown_name_raises(self, cfg):
        with pytest.raises(KeyError):
            settings.set_toggle(cfg, "locators", "ghost", True)

    def test_unknown_section_raises(self, cfg):
        with pytest.raises(KeyError):
            settings.set_toggle(cfg, "weighting", "x", True)


class TestResolveSection:
    def test_picks_the_present_name(self, cfg):
        assert settings.resolve_section(cfg, "optimizers", "optimization") == "optimizers"

    def test_falls_back_to_the_alternative_spelling(self):
        parsed = yaml_io.load("optimization:\n  absorption:\n    enabled: true\n")
        assert settings.resolve_section(parsed, "optimizers", "optimization") == "optimization"

    def test_returns_none_when_neither_exists(self, cfg):
        assert settings.resolve_section(cfg, "weighting") is None

    def test_non_mapping_root_returns_none(self):
        assert settings.resolve_section("x", "locators") is None

    def test_a_non_mapping_section_does_not_count(self, cfg):
        assert settings.resolve_section(cfg, "nodes") is None
