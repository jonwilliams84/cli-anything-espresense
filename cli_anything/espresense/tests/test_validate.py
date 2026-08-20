"""Unit tests for core/validate.py — the config.yaml consistency checker."""

from __future__ import annotations

import pytest

from cli_anything.espresense.core import validate


def _cfg(**over):
    base = {
        "floors": [
            {
                "id": "gf",
                "name": "Ground",
                "rooms": [
                    {"name": "Office", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]},
                    {"name": "Kitchen", "points": [[4, 0], [8, 0], [8, 3], [4, 3]]},
                ],
            }
        ],
        "nodes": [
            {"name": "office-node", "room": "Office", "point": [1.0, 2.0, 2.5]},
            {"name": "kitchen-node", "room": "Kitchen", "point": [5.0, 2.0, 2.5]},
        ],
    }
    base.update(over)
    return base


def codes(report, level="errors"):
    return {f["code"] for f in report[level]}


class TestCleanConfig:
    def test_clean_config_is_ok(self):
        report = validate.check(_cfg())
        assert report["ok"] is True
        assert report["errors"] == []
        assert report["warnings"] == []

    def test_counts_are_reported(self):
        report = validate.check(_cfg())
        assert report["counts"] == {
            "floors": 1,
            "rooms": 2,
            "nodes": 2,
            "errors": 0,
            "warnings": 0,
        }


class TestErrors:
    def test_dangling_room_reference(self):
        cfg = _cfg()
        cfg["nodes"][0]["room"] = "Nowhere"
        report = validate.check(cfg)
        assert validate.DANGLING_ROOM_REF in codes(report)
        assert report["ok"] is False

    def test_whitespace_padded_room_reference(self):
        cfg = _cfg()
        cfg["nodes"][0]["room"] = "Office "
        report = validate.check(cfg)
        assert validate.ROOM_REF_WHITESPACE in codes(report)
        # the stripped name still resolves, so it is NOT also dangling
        assert validate.DANGLING_ROOM_REF not in codes(report)

    def test_whitespace_and_dangling_both_reported(self):
        cfg = _cfg()
        cfg["nodes"][0]["room"] = " Ghost "
        report = validate.check(cfg)
        assert validate.ROOM_REF_WHITESPACE in codes(report)
        assert validate.DANGLING_ROOM_REF in codes(report)

    def test_duplicate_room_name_across_floors(self):
        cfg = _cfg()
        cfg["floors"].append(
            {"id": "ff", "rooms": [{"name": "Office", "points": [[0, 0], [1, 0], [1, 1]]}]}
        )
        report = validate.check(cfg)
        assert validate.DUPLICATE_ROOM_NAME in codes(report)

    def test_duplicate_node_name(self):
        cfg = _cfg()
        cfg["nodes"].append({"name": "office-node", "room": "Kitchen"})
        report = validate.check(cfg)
        assert validate.DUPLICATE_NODE_NAME in codes(report)

    def test_duplicate_floor_id(self):
        cfg = _cfg()
        cfg["floors"].append({"id": "gf", "rooms": []})
        report = validate.check(cfg)
        assert validate.DUPLICATE_FLOOR_ID in codes(report)

    def test_node_missing_name(self):
        cfg = _cfg()
        cfg["nodes"].append({"room": "Office"})
        report = validate.check(cfg)
        assert validate.NODE_MISSING_NAME in codes(report)

    def test_node_blank_name(self):
        cfg = _cfg()
        cfg["nodes"].append({"name": "   ", "room": "Office"})
        report = validate.check(cfg)
        assert validate.NODE_MISSING_NAME in codes(report)

    @pytest.mark.parametrize(
        "point", [[1, 2], [1, 2, 3, 4], ["a", "b", "c"], [1, 2, None], [True, 2, 3]]
    )
    def test_bad_node_point(self, point):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = point
        report = validate.check(cfg)
        assert validate.BAD_NODE_POINT in codes(report)

    def test_boolean_coordinate_is_rejected_not_coerced(self):
        """`point: [true, 2, 3]` is a config bug, not the number 1."""
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [True, 2, 3]
        assert validate.BAD_NODE_POINT in codes(validate.check(cfg))

    def test_int_and_float_coords_accepted(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [1, 2.5, 3]
        assert validate.BAD_NODE_POINT not in codes(validate.check(cfg))

    def test_no_floors_is_an_error(self):
        report = validate.check({"floors": [], "nodes": []})
        assert validate.NO_FLOORS in codes(report)

    def test_non_mapping_root(self):
        report = validate.check(["not", "a", "mapping"])
        assert report["ok"] is False
        assert report["errors"][0]["code"] == "not_a_mapping"


class TestWarnings:
    def test_node_without_room(self):
        cfg = _cfg()
        cfg["nodes"].append({"name": "roaming", "point": [0, 0, 0]})
        report = validate.check(cfg)
        assert validate.NODE_MISSING_ROOM in codes(report, "warnings")

    def test_room_without_node(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"].append({"name": "Attic", "points": [[0, 0], [1, 0], [1, 1]]})
        report = validate.check(cfg)
        assert validate.ROOM_WITHOUT_NODE in codes(report, "warnings")

    def test_degenerate_polygon(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"][0]["points"] = [[0, 0], [1, 1]]
        report = validate.check(cfg)
        assert validate.DEGENERATE_POLYGON in codes(report, "warnings")

    def test_no_nodes_is_a_warning_not_an_error(self):
        report = validate.check({"floors": _cfg()["floors"], "nodes": []})
        assert validate.NO_NODES in codes(report, "warnings")
        assert validate.NO_NODES not in codes(report)

    def test_warnings_alone_keep_ok_true(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"].append({"name": "Attic", "points": [[0, 0], [1, 0], [1, 1]]})
        report = validate.check(cfg)
        assert report["warnings"]
        assert report["ok"] is True


class TestFindingShape:
    def test_every_finding_has_stable_fields(self):
        cfg = _cfg()
        cfg["nodes"][0]["room"] = "Ghost "
        cfg["nodes"].append({"name": "office-node"})
        report = validate.check(cfg)
        assert report["errors"]
        for finding in report["errors"] + report["warnings"]:
            assert finding["level"] in ("error", "warning")
            assert isinstance(finding["code"], str) and finding["code"]
            assert isinstance(finding["message"], str) and finding["message"]

    def test_no_none_values_leak_into_findings(self):
        report = validate.check({"floors": [], "nodes": []})
        for finding in report["errors"] + report["warnings"]:
            assert None not in finding.values()

    def test_check_is_pure(self):
        """Validation must never mutate the config it inspects."""
        import copy

        cfg = _cfg()
        cfg["nodes"][0]["room"] = "Office "
        snapshot = copy.deepcopy(cfg)
        validate.check(cfg)
        assert cfg == snapshot


class TestRegressionRoomsDrift:
    """The failure mode rooms.py was written to repair must now be detected."""

    def test_rename_without_repointing_nodes_is_caught(self):
        from cli_anything.espresense.core import rooms

        cfg = _cfg()
        # simulate a hand-edit that renamed only the polygon
        cfg["floors"][0]["rooms"][0]["name"] = "Study"
        report = validate.check(cfg)
        assert validate.DANGLING_ROOM_REF in codes(report)

        # the supported path fixes it
        cfg2 = _cfg()
        rooms.rename(cfg2, "Office", "Study")
        assert validate.check(cfg2)["ok"] is True


class TestGeometryFindings:
    """The geometry-level drift class: string-valid configs that localise wrong.

    Every check here is warning-level except structurally broken references,
    because unusual-but-deliberate floor plans exist and `config doctor` must
    not fail a push on taste.
    """

    def test_node_point_outside_its_room_is_a_warning(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [99.0, 99.0, 2.5]
        report = validate.check(cfg)
        assert validate.NODE_POINT_OUTSIDE_ROOM in codes(report, "warnings")
        assert report["ok"] is True

    def test_node_point_inside_its_room_is_silent(self):
        assert validate.NODE_POINT_OUTSIDE_ROOM not in codes(_report_all(_cfg()), "warnings")

    def test_node_on_the_room_boundary_is_accepted(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [0.0, 0.0, 2.5]  # exactly on a corner
        assert validate.NODE_POINT_OUTSIDE_ROOM not in codes(_report_all(cfg), "warnings")

    def test_finding_carries_the_node_room_and_point(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [99.0, 99.0, 2.5]
        finding = _by_code(validate.check(cfg), validate.NODE_POINT_OUTSIDE_ROOM)
        assert finding["node"] == "office-node"
        assert finding["room"] == "Office"
        assert finding["point"] == [99.0, 99.0, 2.5]

    def test_malformed_point_is_not_double_reported(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [1.0, 2.0]  # BAD_NODE_POINT already covers this
        report = validate.check(cfg)
        assert validate.BAD_NODE_POINT in codes(report)
        assert validate.NODE_POINT_OUTSIDE_ROOM not in codes(report, "warnings")

    def test_dangling_floor_ref_is_an_error(self):
        cfg = _cfg()
        cfg["nodes"][0]["floors"] = ["attic"]
        report = validate.check(cfg)
        assert validate.DANGLING_FLOOR_REF in codes(report)
        assert report["ok"] is False

    def test_valid_floor_ref_is_silent(self):
        cfg = _cfg()
        cfg["nodes"][0]["floors"] = ["gf"]
        assert validate.DANGLING_FLOOR_REF not in codes(validate.check(cfg))

    def test_retag_fixes_a_dangling_floor_ref(self):
        from cli_anything.espresense.core import floors as floors_core

        cfg = _cfg()
        cfg["nodes"][0]["floors"] = ["gf"]
        floors_core.retag(cfg, "gf", "ground")
        assert validate.check(cfg)["ok"] is True

    def test_bare_id_edit_is_caught(self):
        cfg = _cfg()
        cfg["nodes"][0]["floors"] = ["gf"]
        cfg["floors"][0]["id"] = "ground"  # hand-edit that forgot the nodes
        assert validate.DANGLING_FLOOR_REF in codes(validate.check(cfg))

    def test_unparseable_bounds_is_an_error(self):
        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0], [1, 1]]
        report = validate.check(cfg)
        assert validate.BAD_FLOOR_BOUNDS in codes(report)
        assert report["ok"] is False

    def test_room_escaping_floor_bounds_is_a_warning(self):
        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0, 0], [5, 5, 3]]  # Kitchen reaches x=8
        report = validate.check(cfg)
        assert validate.ROOM_OUTSIDE_FLOOR_BOUNDS in codes(report, "warnings")
        assert report["ok"] is True

    def test_rooms_inside_bounds_are_silent(self):
        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0, 0], [10, 10, 3]]
        assert validate.ROOM_OUTSIDE_FLOOR_BOUNDS not in codes(_report_all(cfg), "warnings")

    def test_no_bounds_means_no_bounds_checks(self):
        cfg = _cfg()  # the fixture declares no bounds at all
        report = _report_all(cfg)
        assert validate.ROOM_OUTSIDE_FLOOR_BOUNDS not in codes(report, "warnings")
        assert validate.NODE_POINT_OUTSIDE_BOUNDS not in codes(report, "warnings")

    def test_node_above_the_ceiling_is_a_warning(self):
        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0, 0], [10, 10, 2.0]]
        cfg["nodes"][0]["point"] = [1.0, 2.0, 2.5]  # 2.5 m in a 2.0 m room
        assert validate.NODE_POINT_OUTSIDE_BOUNDS in codes(_report_all(cfg), "warnings")

    def test_node_bounds_check_follows_explicit_floor_refs(self):
        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0, 0], [10, 10, 2.0]]
        cfg["nodes"][0]["floors"] = ["gf"]
        cfg["nodes"][0]["room"] = None
        cfg["nodes"][0]["point"] = [1.0, 2.0, 9.0]
        assert validate.NODE_POINT_OUTSIDE_BOUNDS in codes(_report_all(cfg), "warnings")

    def test_overlapping_rooms_are_a_warning(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"][1]["points"] = [[2, 0], [6, 0], [6, 3], [2, 3]]
        report = validate.check(cfg)
        assert validate.ROOM_OVERLAP in codes(report, "warnings")
        assert report["ok"] is True

    def test_rooms_sharing_a_wall_are_not_flagged(self):
        # The fixture is two rooms meeting at x=4 — the normal case.
        assert validate.ROOM_OVERLAP not in codes(_report_all(_cfg()), "warnings")

    def test_overlap_finding_names_both_rooms(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"][1]["points"] = [[2, 0], [6, 0], [6, 3], [2, 3]]
        finding = _by_code(validate.check(cfg), validate.ROOM_OVERLAP, "warnings")
        assert {finding["room"], finding["other_room"]} == {"Office", "Kitchen"}

    def test_same_footprint_on_two_floors_is_not_an_overlap(self):
        cfg = _cfg()
        cfg["floors"].append(
            {
                "id": "ff",
                "rooms": [{"name": "Bedroom", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]}],
            }
        )
        cfg["nodes"].append({"name": "bed-node", "room": "Bedroom", "point": [1.0, 1.0, 2.2]})
        assert validate.ROOM_OVERLAP not in codes(_report_all(cfg), "warnings")

    def test_degenerate_polygons_are_skipped_by_geometry_checks(self):
        cfg = _cfg()
        cfg["floors"][0]["rooms"][0]["points"] = [[0, 0], [1, 1]]
        report = validate.check(cfg)
        assert validate.DEGENERATE_POLYGON in codes(report, "warnings")
        assert validate.NODE_POINT_OUTSIDE_ROOM not in codes(report, "warnings")
        assert validate.ROOM_OVERLAP not in codes(report, "warnings")

    def test_geometry_checks_do_not_mutate_the_config(self):
        import copy

        cfg = _cfg()
        cfg["floors"][0]["bounds"] = [[0, 0, 0], [10, 10, 3]]
        cfg["nodes"][0]["point"] = [99.0, 99.0, 2.5]
        snapshot = copy.deepcopy(cfg)
        validate.check(cfg)
        assert cfg == snapshot

    def test_strict_mode_callers_can_gate_on_geometry_warnings(self):
        cfg = _cfg()
        cfg["nodes"][0]["point"] = [99.0, 99.0, 2.5]
        report = validate.check(cfg)
        # `config doctor --strict` fails on any warning; that is the hook.
        assert report["counts"]["warnings"] >= 1


def _report_all(cfg):
    return validate.check(cfg)


def _by_code(report, code, level="errors"):
    """First finding with `code`, searching errors and warnings alike."""
    for finding in report["errors"] + report["warnings"]:
        if finding["code"] == code:
            return finding
    raise AssertionError(f"{code} not found in report")


class TestDeviceRegistryFindings:
    """`devices:` checks — the block `devices ...-config` commands write."""

    def test_a_clean_registry_is_silent(self):
        cfg = _cfg(devices=[{"id": "irk:aaa", "name": "Phone", "rssi@1m": -65}])
        report = validate.check(cfg)
        assert report["ok"] is True
        assert report["warnings"] == []

    def test_no_devices_block_is_not_a_finding(self):
        assert validate.check(_cfg())["warnings"] == []

    def test_duplicate_device_id_is_an_error(self):
        cfg = _cfg(devices=[{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}])
        report = validate.check(cfg)
        assert validate.DUPLICATE_DEVICE_ID in codes(report)
        assert report["ok"] is False

    def test_duplicate_finding_names_the_id(self):
        cfg = _cfg(devices=[{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}])
        assert _by_code(validate.check(cfg), validate.DUPLICATE_DEVICE_ID)["device"] == "dup"

    def test_missing_id_is_an_error(self):
        cfg = _cfg(devices=[{"name": "Nameless"}])
        assert validate.DEVICE_MISSING_ID in codes(validate.check(cfg))

    def test_blank_id_is_an_error(self):
        cfg = _cfg(devices=[{"id": "   ", "name": "A"}])
        assert validate.DEVICE_MISSING_ID in codes(validate.check(cfg))

    def test_a_non_mapping_entry_is_an_error(self):
        cfg = _cfg(devices=["irk:aaa"])
        assert validate.DEVICE_MISSING_ID in codes(validate.check(cfg))

    def test_non_numeric_reference_rssi_is_an_error(self):
        cfg = _cfg(devices=[{"id": "a", "name": "A", "rssi@1m": "loud"}])
        assert validate.BAD_DEVICE_RSSI in codes(validate.check(cfg))

    def test_boolean_reference_rssi_is_an_error(self):
        cfg = _cfg(devices=[{"id": "a", "name": "A", "rssi@1m": True}])
        assert validate.BAD_DEVICE_RSSI in codes(validate.check(cfg))

    def test_the_underscored_alias_is_checked_too(self):
        cfg = _cfg(devices=[{"id": "a", "name": "A", "rssi_at_1m": "loud"}])
        assert validate.BAD_DEVICE_RSSI in codes(validate.check(cfg))

    def test_null_reference_rssi_is_tolerated(self):
        cfg = _cfg(devices=[{"id": "a", "name": "A", "rssi@1m": None}])
        assert validate.BAD_DEVICE_RSSI not in codes(validate.check(cfg))

    def test_unnamed_device_is_only_a_warning(self):
        cfg = _cfg(devices=[{"id": "irk:aaa"}])
        report = validate.check(cfg)
        assert validate.DEVICE_WITHOUT_NAME in codes(report, "warnings")
        assert report["ok"] is True

    def test_devices_block_that_is_not_a_list_is_an_error(self):
        report = validate.check(_cfg(devices={"id": "a"}))
        assert report["ok"] is False

    def test_device_checks_do_not_mutate_the_config(self):
        import copy

        cfg = _cfg(devices=[{"id": "a"}, {"id": "a", "rssi@1m": "loud"}])
        snapshot = copy.deepcopy(cfg)
        validate.check(cfg)
        assert cfg == snapshot


class TestLocatorFindings:
    def test_all_locators_disabled_is_a_warning(self):
        cfg = _cfg(
            locators={"nadaraya_watson": {"enabled": False}, "nelder_mead": {"enabled": False}}
        )
        report = validate.check(cfg)
        assert validate.NO_LOCATOR_ENABLED in codes(report, "warnings")
        assert report["ok"] is True

    def test_the_warning_lists_the_locators(self):
        cfg = _cfg(locators={"nadaraya_watson": {"enabled": False}})
        finding = _by_code(validate.check(cfg), validate.NO_LOCATOR_ENABLED)
        assert finding["locators"] == ["nadaraya_watson"]

    def test_one_enabled_locator_clears_it(self):
        cfg = _cfg(locators={"a": {"enabled": False}, "b": {"enabled": True}})
        assert validate.NO_LOCATOR_ENABLED not in codes(validate.check(cfg), "warnings")

    def test_absent_enabled_key_counts_as_on(self):
        cfg = _cfg(locators={"nearest_node": {"max_distance": 3}})
        assert validate.NO_LOCATOR_ENABLED not in codes(validate.check(cfg), "warnings")

    def test_no_locators_block_is_not_a_finding(self):
        assert validate.NO_LOCATOR_ENABLED not in codes(validate.check(_cfg()), "warnings")

    def test_empty_locators_block_is_not_a_finding(self):
        assert validate.NO_LOCATOR_ENABLED not in codes(
            validate.check(_cfg(locators={})), "warnings"
        )

    def test_a_malformed_locators_block_does_not_crash_the_checker(self):
        report = validate.check(_cfg(locators="nadaraya_watson"))
        assert isinstance(report["warnings"], list)
