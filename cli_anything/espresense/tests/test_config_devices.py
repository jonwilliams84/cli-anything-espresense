"""Unit tests for `core/config_devices.py` — the config.yaml `devices:` block.

Pure module, so these are real end-to-end exercises of the logic: a YAML
string in, a mutated YAML string out, no mocks anywhere.
"""

from __future__ import annotations

import pytest

from cli_anything.espresense.core import config_devices as cd
from cli_anything.espresense.utils import yaml_io

DEVICES_YAML = """\
devices:
  - id: "irk:aaa"
    name: Phone
    "rssi@1m": -65
  - id: tile-1
  - id: watch
    name: Watch
    rssi_at_1m: -59
    unknown_future_key: keep-me
"""


@pytest.fixture()
def cfg():
    return yaml_io.load(DEVICES_YAML)


class TestListAndGet:
    def test_lists_every_entry(self, cfg):
        rows = cd.list_devices(cfg)
        assert [r["id"] for r in rows] == ["irk:aaa", "tile-1", "watch"]

    def test_reads_the_at_1m_alias(self, cfg):
        assert cd.list_devices(cfg)[0]["rssi_at_1m"] == -65.0

    def test_reads_the_underscored_alias_too(self, cfg):
        assert cd.list_devices(cfg)[2]["rssi_at_1m"] == -59.0

    def test_unknown_keys_are_surfaced_not_dropped(self, cfg):
        assert cd.list_devices(cfg)[2]["extra"] == {"unknown_future_key": "keep-me"}

    def test_no_extra_key_means_none(self, cfg):
        assert cd.list_devices(cfg)[0]["extra"] is None

    def test_missing_rssi_is_none(self, cfg):
        assert cd.list_devices(cfg)[1]["rssi_at_1m"] is None

    def test_non_numeric_rssi_reads_as_none_rather_than_raising(self):
        parsed = yaml_io.load('devices:\n  - id: a\n    "rssi@1m": loud\n')
        assert cd.list_devices(parsed)[0]["rssi_at_1m"] is None

    def test_explicit_null_rssi_reads_as_none(self):
        parsed = yaml_io.load('devices:\n  - id: a\n    "rssi@1m":\n')
        assert cd.list_devices(parsed)[0]["rssi_at_1m"] is None

    def test_empty_config_lists_nothing(self):
        assert cd.list_devices(yaml_io.load("floors: []\n")) == []

    def test_non_mapping_entries_are_skipped(self):
        parsed = yaml_io.load("devices:\n  - just-a-string\n  - id: real\n")
        assert [r["id"] for r in cd.list_devices(parsed)] == ["real"]

    def test_get_returns_the_row(self, cfg):
        assert cd.get(cfg, "watch")["name"] == "Watch"

    def test_get_raises_for_unknown_id(self, cfg):
        with pytest.raises(KeyError):
            cd.get(cfg, "nope")

    def test_find_returns_the_live_mapping(self, cfg):
        entry = cd.find(cfg, "tile-1")
        entry["name"] = "Keys"
        assert cd.get(cfg, "tile-1")["name"] == "Keys"

    def test_find_returns_none_when_absent(self, cfg):
        assert cd.find(cfg, "nope") is None


class TestAdd:
    def test_appends_a_device(self, cfg):
        out = cd.add(cfg, "beacon-9", name="Beacon", rssi_at_1m=-70)
        assert out["added"] is True
        assert cd.get(cfg, "beacon-9")["name"] == "Beacon"

    def test_creates_the_block_when_absent(self):
        parsed = yaml_io.load("floors: []\n")
        cd.add(parsed, "first")
        assert [r["id"] for r in cd.list_devices(parsed)] == ["first"]

    def test_integral_rssi_stays_integral_in_yaml(self, cfg):
        cd.add(cfg, "beacon-9", rssi_at_1m=-70.0)
        assert '"rssi@1m": -70\n' in yaml_io.dumps(cfg)

    def test_fractional_rssi_is_preserved(self, cfg):
        cd.add(cfg, "beacon-9", rssi_at_1m=-70.5)
        assert cd.get(cfg, "beacon-9")["rssi_at_1m"] == -70.5

    def test_id_with_a_colon_is_quoted(self, cfg):
        cd.add(cfg, "irk:ffff")
        assert '- id: "irk:ffff"' in yaml_io.dumps(cfg)

    def test_plain_id_is_not_quoted(self, cfg):
        cd.add(cfg, "beacon9")
        assert "- id: beacon9" in yaml_io.dumps(cfg)

    def test_duplicate_id_is_refused(self, cfg):
        with pytest.raises(cd.DeviceConfigError, match="already exists"):
            cd.add(cfg, "tile-1")

    def test_empty_id_is_refused(self, cfg):
        with pytest.raises(cd.DeviceConfigError, match="non-empty"):
            cd.add(cfg, "   ")

    def test_none_id_is_refused(self, cfg):
        with pytest.raises(cd.DeviceConfigError):
            cd.add(cfg, None)

    def test_id_is_stripped(self, cfg):
        cd.add(cfg, "  spaced  ")
        assert cd.find(cfg, "spaced") is not None

    def test_optional_fields_are_omitted(self, cfg):
        cd.add(cfg, "bare")
        assert set(cd.find(cfg, "bare").keys()) == {"id"}


class TestUpdate:
    def test_reports_not_found(self, cfg):
        out = cd.update(cfg, "ghost", name="X")
        assert out == {"found": False, "changed": [], "before": None, "after": None}

    def test_sets_a_name(self, cfg):
        out = cd.update(cfg, "tile-1", name="Keys")
        assert out["changed"] == ["name"]
        assert out["after"]["name"] == "Keys"

    def test_unmentioned_fields_are_left_alone(self, cfg):
        cd.update(cfg, "irk:aaa", name="Handset")
        assert cd.get(cfg, "irk:aaa")["rssi_at_1m"] == -65.0

    def test_explicit_none_clears_the_name(self, cfg):
        out = cd.update(cfg, "irk:aaa", name=None)
        assert out["changed"] == ["name"]
        assert "name" not in cd.find(cfg, "irk:aaa")

    def test_clearing_an_absent_name_changes_nothing(self, cfg):
        assert cd.update(cfg, "tile-1", name=None)["changed"] == []

    def test_setting_the_same_name_is_a_no_op(self, cfg):
        assert cd.update(cfg, "irk:aaa", name="Phone")["changed"] == []

    def test_sets_rssi(self, cfg):
        out = cd.update(cfg, "tile-1", rssi_at_1m=-72)
        assert out["changed"] == ["rssi_at_1m"]
        assert cd.get(cfg, "tile-1")["rssi_at_1m"] == -72.0

    def test_new_rssi_key_is_quoted(self, cfg):
        cd.update(cfg, "tile-1", rssi_at_1m=-72)
        assert '"rssi@1m": -72' in yaml_io.dumps(cfg)

    def test_setting_rssi_normalises_the_underscored_alias(self, cfg):
        cd.update(cfg, "watch", rssi_at_1m=-61)
        entry = cd.find(cfg, "watch")
        assert "rssi_at_1m" not in entry
        assert entry[cd.RSSI_KEY] == -61

    def test_clearing_rssi_removes_every_alias(self, cfg):
        out = cd.update(cfg, "watch", rssi_at_1m=None)
        assert out["changed"] == ["rssi_at_1m"]
        assert cd.get(cfg, "watch")["rssi_at_1m"] is None

    def test_setting_the_same_rssi_is_a_no_op(self, cfg):
        assert cd.update(cfg, "irk:aaa", rssi_at_1m=-65)["changed"] == []

    def test_normalising_the_alias_still_counts_as_a_change(self, cfg):
        # same number, different on-disk key: the file does change
        assert cd.update(cfg, "watch", rssi_at_1m=-59)["changed"] == ["rssi_at_1m"]

    def test_clearing_absent_rssi_changes_nothing(self, cfg):
        assert cd.update(cfg, "tile-1", rssi_at_1m=None)["changed"] == []

    def test_renames_the_id(self, cfg):
        out = cd.update(cfg, "tile-1", new_id="tile-front-door")
        assert out["changed"] == ["id"]
        assert cd.find(cfg, "tile-1") is None
        assert cd.find(cfg, "tile-front-door") is not None

    def test_rename_onto_an_existing_id_is_refused(self, cfg):
        with pytest.raises(cd.DeviceConfigError, match="already exists"):
            cd.update(cfg, "tile-1", new_id="watch")

    def test_rename_to_the_same_id_is_a_no_op(self, cfg):
        assert cd.update(cfg, "tile-1", new_id="tile-1")["changed"] == []

    def test_rename_to_blank_is_refused(self, cfg):
        with pytest.raises(cd.DeviceConfigError, match="non-empty"):
            cd.update(cfg, "tile-1", new_id="   ")

    def test_several_fields_at_once(self, cfg):
        out = cd.update(cfg, "tile-1", new_id="keys", name="Keys", rssi_at_1m=-70)
        assert out["changed"] == ["id", "name", "rssi_at_1m"]
        assert out["after"] == {
            "id": "keys",
            "name": "Keys",
            "rssi_at_1m": -70.0,
            "extra": None,
        }

    def test_before_snapshot_predates_the_edit(self, cfg):
        out = cd.update(cfg, "irk:aaa", name="Handset")
        assert out["before"]["name"] == "Phone"


class TestRemove:
    def test_removes_the_entry(self, cfg):
        out = cd.remove(cfg, "tile-1")
        assert out["removed"] is True
        assert out["entry"]["id"] == "tile-1"
        assert cd.find(cfg, "tile-1") is None

    def test_leaves_the_others(self, cfg):
        cd.remove(cfg, "tile-1")
        assert [r["id"] for r in cd.list_devices(cfg)] == ["irk:aaa", "watch"]

    def test_unknown_id_reports_false(self, cfg):
        assert cd.remove(cfg, "ghost") == {"removed": False, "id": "ghost", "entry": None}

    def test_missing_block_reports_false(self):
        assert cd.remove(yaml_io.load("floors: []\n"), "x")["removed"] is False


class TestRoundTrip:
    def test_comments_and_neighbours_survive_an_edit(self):
        text = "# top comment\ndevices:\n  - id: a\n    name: A\nfloors: []\n"
        parsed = yaml_io.load(text)
        cd.update(parsed, "a", name="B")
        out = yaml_io.dumps(parsed)
        assert out.startswith("# top comment")
        assert "floors: []" in out

    def test_add_then_remove_returns_the_original_document(self):
        text = "devices:\n  - id: a\n    name: A\n"
        parsed = yaml_io.load(text)
        cd.add(parsed, "b", name="B", rssi_at_1m=-60)
        cd.remove(parsed, "b")
        assert yaml_io.dumps(parsed) == text
