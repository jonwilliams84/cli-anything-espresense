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
