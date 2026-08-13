"""Unit tests for core/floors.py plus the geometry-aware helpers added to
core/rooms.py and core/nodes.py.

These operate on a real ruamel round-trip of a config.yaml (not plain dicts),
because half the risk in these functions is *how* they write coordinates back:
a bare Python list dumps as a block ladder and visually shreds a hand-authored
file, so the flow-style assertions below are load-bearing.
"""

from __future__ import annotations

import pytest

from cli_anything.espresense.core import floors as floors_core
from cli_anything.espresense.core import geometry
from cli_anything.espresense.core import nodes as nodes_core
from cli_anything.espresense.core import rooms as rooms_core
from cli_anything.espresense.utils import yaml_io


SAMPLE = """\
# hand-authored
floors:
  - id: gf
    name: Ground Floor
    bounds: [[0, 0, 0], [10, 10, 3]]
    rooms:
      - name: Office
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
      - name: Kitchen
        points: [[4, 0], [8, 0], [8, 3], [4, 3]]
  - id: ff
    name: First Floor
    rooms:
      - name: Bedroom
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
nodes:
  - name: office-node
    room: Office
    floors: ["gf"]
    point: [1.0, 2.0, 2.5]
  - name: kitchen-node
    room: Kitchen
    point: [5.0, 2.0, 2.5]
  - name: bedroom-node
    room: Bedroom
    floors: ["ff"]
    point: [1.0, 1.0, 2.2]
"""


@pytest.fixture()
def parsed():
    return yaml_io.load(SAMPLE)


# ─────────────────────────────────────────────── floors: add / rename / retag


class TestAddFloor:
    def test_appends_an_empty_floor(self, parsed):
        out = floors_core.add_floor(parsed, "bs", name="Basement")
        assert out["added"] is True
        assert [fl["id"] for fl in parsed["floors"]] == ["gf", "ff", "bs"]
        assert parsed["floors"][-1]["rooms"] == []

    def test_bounds_are_normalized_and_flow_styled(self, parsed):
        floors_core.add_floor(parsed, "bs", bounds=[[5, 5, 2], [0, 0, 0]])
        text = yaml_io.dumps(parsed)
        assert "bounds: [[0.0, 0.0, 0.0], [5.0, 5.0, 2.0]]" in text

    def test_name_and_bounds_are_optional(self, parsed):
        out = floors_core.add_floor(parsed, "bs")
        assert out["name"] is None
        assert out["bounds"] is None
        assert "bounds" not in parsed["floors"][-1]

    def test_duplicate_id_is_refused(self, parsed):
        with pytest.raises(ValueError, match="already exists"):
            floors_core.add_floor(parsed, "gf")

    def test_empty_id_is_refused(self, parsed):
        with pytest.raises(ValueError, match="non-empty"):
            floors_core.add_floor(parsed, "   ")

    def test_bad_bounds_are_refused(self, parsed):
        with pytest.raises(geometry.GeometryError):
            floors_core.add_floor(parsed, "bs", bounds=[[0, 0], [1, 1]])

    def test_creates_the_floors_key_when_absent(self):
        cfg = yaml_io.load("nodes: []\n")
        floors_core.add_floor(cfg, "gf", name="Ground")
        assert [fl["id"] for fl in cfg["floors"]] == ["gf"]

    def test_id_is_stripped(self, parsed):
        floors_core.add_floor(parsed, "  bs  ")
        assert parsed["floors"][-1]["id"] == "bs"


class TestRenameFloor:
    def test_sets_the_display_name_only(self, parsed):
        out = floors_core.rename_floor(parsed, "gf", "Downstairs")
        assert out == {"found": True, "id": "gf", "before": "Ground Floor", "after": "Downstairs"}
        assert parsed["floors"][0]["id"] == "gf"

    def test_unknown_floor_raises(self, parsed):
        with pytest.raises(KeyError):
            floors_core.rename_floor(parsed, "attic", "Attic")


class TestRetag:
    def test_changes_id_and_node_refs_together(self, parsed):
        out = floors_core.retag(parsed, "gf", "ground")
        assert out["id_changed"] is True
        assert out["nodes_repointed"] == 1
        assert parsed["floors"][0]["id"] == "ground"
        assert parsed["nodes"][0]["floors"] == ["ground"]

    def test_leaves_other_floors_refs_alone(self, parsed):
        floors_core.retag(parsed, "gf", "ground")
        assert parsed["nodes"][2]["floors"] == ["ff"]

    def test_noop_when_ids_match(self, parsed):
        out = floors_core.retag(parsed, "gf", "gf")
        assert out["id_changed"] is False
        assert out["nodes_repointed"] == 0

    def test_refuses_to_collide_with_an_existing_id(self, parsed):
        with pytest.raises(ValueError, match="already exists"):
            floors_core.retag(parsed, "gf", "ff")

    def test_unknown_floor_raises(self, parsed):
        with pytest.raises(KeyError):
            floors_core.retag(parsed, "attic", "loft")

    def test_empty_new_id_is_refused(self, parsed):
        with pytest.raises(ValueError, match="non-empty"):
            floors_core.retag(parsed, "gf", "  ")

    def test_repoints_multiple_nodes(self, parsed):
        parsed["nodes"][1]["floors"] = ["gf"]
        assert floors_core.retag(parsed, "gf", "ground")["nodes_repointed"] == 2


# ─────────────────────────────────────────────── floors: bounds


class TestSetBounds:
    def test_replaces_bounds_and_reports_the_previous(self, parsed):
        out = floors_core.set_bounds(parsed, "gf", [[0, 0, 0], [12, 9, 2.4]])
        assert out["before"] == [[0, 0, 0], [10, 10, 3]]
        assert out["after"] == [[0.0, 0.0, 0.0], [12.0, 9.0, 2.4]]

    def test_adds_bounds_to_a_floor_that_had_none(self, parsed):
        out = floors_core.set_bounds(parsed, "ff", [[0, 0, 0], [4, 3, 2.4]])
        assert out["before"] is None
        assert "bounds: [[0.0, 0.0, 0.0], [4.0, 3.0, 2.4]]" in yaml_io.dumps(parsed)

    def test_bad_bounds_raise_before_mutating(self, parsed):
        with pytest.raises(geometry.GeometryError):
            floors_core.set_bounds(parsed, "gf", [[0, 0, 0]])
        assert [list(c) for c in parsed["floors"][0]["bounds"]] == [[0, 0, 0], [10, 10, 3]]

    def test_unknown_floor_raises(self, parsed):
        with pytest.raises(KeyError):
            floors_core.set_bounds(parsed, "attic", [[0, 0, 0], [1, 1, 1]])


class TestFitBounds:
    def test_derives_bounds_from_the_rooms(self, parsed):
        out = floors_core.fit_bounds(parsed, "gf")
        assert out["after"] == [[0.0, 0.0, 0.0], [8.0, 3.0, 3.0]]
        assert out["rooms_considered"] == 2

    def test_margin_pads_every_side(self, parsed):
        out = floors_core.fit_bounds(parsed, "gf", margin=0.5)
        assert out["after"] == [[-0.5, -0.5, 0.0], [8.5, 3.5, 3.0]]

    def test_keeps_the_existing_z_extent(self, parsed):
        # gf already declares a 0..3 ceiling; fitting x/y must not flatten it.
        assert floors_core.fit_bounds(parsed, "gf")["after"][1][2] == 3.0

    def test_derives_z_from_nodes_when_no_bounds_exist(self, parsed):
        out = floors_core.fit_bounds(parsed, "ff")
        assert out["before"] is None
        assert out["after"] == [[0.0, 0.0, 0.0], [4.0, 3.0, 3.0]]

    def test_explicit_z_wins(self, parsed):
        out = floors_core.fit_bounds(parsed, "gf", z_min=0.2, z_max=2.4)
        assert out["after"][0][2] == 0.2
        assert out["after"][1][2] == 2.4

    def test_node_z_above_the_default_raises_the_ceiling(self, parsed):
        parsed["nodes"][2]["point"] = [1.0, 1.0, 3.6]
        assert floors_core.fit_bounds(parsed, "ff")["after"][1][2] == 3.6

    def test_unparseable_existing_bounds_are_ignored_not_fatal(self, parsed):
        parsed["floors"][0]["bounds"] = ["nonsense"]
        out = floors_core.fit_bounds(parsed, "gf")
        assert out["before"] is None
        assert out["after"][1][:2] == [8.0, 3.0]

    def test_floor_without_rooms_is_refused(self, parsed):
        floors_core.add_floor(parsed, "bs")
        with pytest.raises(ValueError, match="no room polygons"):
            floors_core.fit_bounds(parsed, "bs")

    def test_result_is_flow_styled(self, parsed):
        floors_core.fit_bounds(parsed, "gf")
        assert "bounds: [[0.0, 0.0, 0.0], [8.0, 3.0, 3.0]]" in yaml_io.dumps(parsed)

    def test_fit_after_move_tracks_the_room(self, parsed):
        rooms_core.move_room(parsed, "Kitchen", 4, 0)
        assert floors_core.fit_bounds(parsed, "gf")["after"][1][0] == 12.0


# ─────────────────────────────────────────────── floors: delete


class TestDeleteFloor:
    def test_removes_the_floor_and_its_rooms(self, parsed):
        out = floors_core.delete_floor(parsed, "ff")
        assert out["deleted"] is True
        assert out["rooms_removed"] == 1
        assert out["room_names"] == ["Bedroom"]
        assert [fl["id"] for fl in parsed["floors"]] == ["gf"]

    def test_reports_orphaned_and_referencing_nodes(self, parsed):
        out = floors_core.delete_floor(parsed, "ff")
        assert out["orphaned_nodes"] == ["bedroom-node"]
        assert out["nodes_referencing"] == ["bedroom-node"]

    def test_does_not_rewrite_nodes(self, parsed):
        floors_core.delete_floor(parsed, "ff")
        assert parsed["nodes"][2]["room"] == "Bedroom"

    def test_unknown_floor_is_reported_not_raised(self, parsed):
        out = floors_core.delete_floor(parsed, "attic")
        assert out["deleted"] is False
        assert len(parsed["floors"]) == 2

    def test_reports_nodes_without_explicit_floor_refs(self, parsed):
        out = floors_core.delete_floor(parsed, "gf")
        assert sorted(out["orphaned_nodes"]) == ["kitchen-node", "office-node"]
        assert out["nodes_referencing"] == ["office-node"]


# ─────────────────────────────────────────────── rooms: geometry reporting


class TestGeometryReport:
    def test_reports_area_and_centroid_per_room(self, parsed):
        rows = rooms_core.geometry_report(parsed)
        assert [r["room_name"] for r in rows] == ["Office", "Kitchen", "Bedroom"]
        assert rows[0]["area"] == 12.0
        assert rows[0]["centroid"] == [2.0, 1.5]
        assert rows[0]["perimeter"] == 14.0

    def test_floor_filter(self, parsed):
        rows = rooms_core.geometry_report(parsed, floor_id="ff")
        assert [r["room_name"] for r in rows] == ["Bedroom"]

    def test_node_containment_split(self, parsed):
        rows = {r["room_name"]: r for r in rooms_core.geometry_report(parsed)}
        assert rows["Office"]["nodes_inside"] == ["office-node"]
        assert rows["Office"]["nodes_outside"] == []

    def test_node_outside_its_room_is_flagged(self, parsed):
        parsed["nodes"][0]["point"] = [9.0, 9.0, 2.5]
        rows = {r["room_name"]: r for r in rooms_core.geometry_report(parsed)}
        assert rows["Office"]["nodes_outside"] == ["office-node"]
        assert rows["Office"]["nodes_inside"] == []

    def test_unusable_polygon_reports_an_error_field(self, parsed):
        parsed["floors"][0]["rooms"][0]["points"] = [[0, 0], [1, 1]]
        row = rooms_core.geometry_report(parsed, floor_id="gf")[0]
        assert row["area"] is None
        assert "at least 3" in row["error"]

    def test_nodes_with_2d_points_are_skipped_not_fatal(self, parsed):
        parsed["nodes"][0]["point"] = [1.0]
        rows = {r["room_name"]: r for r in rooms_core.geometry_report(parsed)}
        assert rows["Office"]["nodes_inside"] == []


class TestLocatePoint:
    def test_finds_the_containing_room(self, parsed):
        hits = rooms_core.locate_point(parsed, 5, 1, floor_id="gf")
        assert [h["room_name"] for h in hits] == ["Kitchen"]

    def test_point_in_no_room_returns_empty(self, parsed):
        assert rooms_core.locate_point(parsed, 50, 50) == []

    def test_same_footprint_on_two_floors_returns_both(self, parsed):
        hits = rooms_core.locate_point(parsed, 1, 1)
        assert {h["room_name"] for h in hits} == {"Office", "Bedroom"}

    def test_results_are_sorted_by_distance_from_centroid(self, parsed):
        hits = rooms_core.locate_point(parsed, 1, 1)
        assert hits == sorted(hits, key=lambda h: h["distance_from_centroid"])

    def test_boundary_hit_is_marked(self, parsed):
        hits = rooms_core.locate_point(parsed, 4, 1, floor_id="gf")
        assert all(h["on_boundary"] for h in hits)
        assert {h["room_name"] for h in hits} == {"Office", "Kitchen"}

    def test_unusable_polygons_are_skipped(self, parsed):
        parsed["floors"][0]["rooms"][0]["points"] = [[0, 0], [1, 1]]
        assert [h["room_name"] for h in rooms_core.locate_point(parsed, 1, 1, floor_id="gf")] == []


class TestFindOverlaps:
    def test_wall_sharing_rooms_are_not_overlaps(self, parsed):
        assert rooms_core.find_overlaps(parsed) == []

    def test_detects_an_overlap_after_a_move(self, parsed):
        rooms_core.move_room(parsed, "Kitchen", -2, 0)
        assert rooms_core.find_overlaps(parsed) == [
            {"floor_id": "gf", "room_a": "Office", "room_b": "Kitchen"}
        ]

    def test_does_not_compare_across_floors(self, parsed):
        # Office and Bedroom share a footprint on different floors: normal.
        assert rooms_core.find_overlaps(parsed, floor_id="ff") == []

    def test_unusable_polygons_are_skipped(self, parsed):
        parsed["floors"][0]["rooms"][0]["points"] = [[0, 0], [1, 1]]
        assert rooms_core.find_overlaps(parsed, floor_id="gf") == []


# ─────────────────────────────────────────────── rooms: polygon edits


class TestSetPoints:
    def test_replaces_the_polygon(self, parsed):
        out = rooms_core.set_points(parsed, "Office", [[0, 0], [5, 0], [5, 4], [0, 4]])
        assert out["point_count"] == 4
        assert out["before"] == [[0, 0], [4, 0], [4, 3], [0, 3]]
        assert rooms_core.geometry_report(parsed, floor_id="gf")[0]["area"] == 20.0

    def test_writes_flow_style(self, parsed):
        rooms_core.set_points(parsed, "Office", [[0, 0], [5, 0], [5, 4], [0, 4]])
        assert "points: [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]" in yaml_io.dumps(parsed)

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            rooms_core.set_points(parsed, "Ghost", [[0, 0], [1, 0], [1, 1]])

    def test_too_few_points_raises_before_mutating(self, parsed):
        with pytest.raises(geometry.GeometryError):
            rooms_core.set_points(parsed, "Office", [[0, 0], [1, 1]])
        assert len(parsed["floors"][0]["rooms"][0]["points"]) == 4

    def test_name_is_matched_after_stripping(self, parsed):
        assert rooms_core.set_points(parsed, " Office ", [[0, 0], [1, 0], [1, 1]])["found"] is True


class TestMoveRoom:
    def test_translates_every_vertex(self, parsed):
        out = rooms_core.move_room(parsed, "Office", 1, 2)
        assert out["after"] == [[1.0, 2.0], [5.0, 2.0], [5.0, 5.0], [1.0, 5.0]]

    def test_preserves_area(self, parsed):
        before = rooms_core.geometry_report(parsed, floor_id="gf")[0]["area"]
        rooms_core.move_room(parsed, "Office", 3, -1)
        assert rooms_core.geometry_report(parsed, floor_id="gf")[0]["area"] == before

    def test_negative_deltas(self, parsed):
        assert rooms_core.move_room(parsed, "Office", -1, -1)["after"][0] == [-1.0, -1.0]

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            rooms_core.move_room(parsed, "Ghost", 1, 1)


class TestScaleRoom:
    def test_about_centroid_keeps_the_room_in_place(self, parsed):
        out = rooms_core.scale_room(parsed, "Office", 2)
        assert out["about"] == "centroid"
        assert out["area_after"] == pytest.approx(4 * out["area_before"])
        assert rooms_core.centroid_of(parsed, "Office") == pytest.approx((2.0, 1.5))

    def test_about_origin(self, parsed):
        out = rooms_core.scale_room(parsed, "Office", 2, about_origin=True)
        assert out["about"] == "origin"
        assert out["after"][0] == [0.0, 0.0]
        assert out["after"][2] == [8.0, 6.0]

    def test_shrinking(self, parsed):
        out = rooms_core.scale_room(parsed, "Office", 0.5)
        assert out["area_after"] == pytest.approx(3.0)

    def test_zero_factor_raises(self, parsed):
        with pytest.raises(geometry.GeometryError):
            rooms_core.scale_room(parsed, "Office", 0)

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            rooms_core.scale_room(parsed, "Ghost", 2)


class TestSetColor:
    def test_sets_a_colour(self, parsed):
        out = rooms_core.set_color(parsed, "Office", "#a3c9f9")
        assert out["before"] is None
        assert parsed["floors"][0]["rooms"][0]["color"] == "#a3c9f9"

    def test_overwrites_and_reports_the_previous(self, parsed):
        rooms_core.set_color(parsed, "Office", "#111111")
        assert rooms_core.set_color(parsed, "Office", "#222222")["before"] == "#111111"

    def test_none_clears_the_colour(self, parsed):
        rooms_core.set_color(parsed, "Office", "#111111")
        out = rooms_core.set_color(parsed, "Office", None)
        assert out["after"] is None
        assert "color" not in parsed["floors"][0]["rooms"][0]

    def test_clearing_an_unset_colour_is_a_noop(self, parsed):
        assert rooms_core.set_color(parsed, "Office", None)["before"] is None

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            rooms_core.set_color(parsed, "Ghost", "#fff")


class TestCentroidOf:
    def test_returns_the_polygon_centroid(self, parsed):
        assert rooms_core.centroid_of(parsed, "Kitchen") == pytest.approx((6.0, 1.5))

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            rooms_core.centroid_of(parsed, "Ghost")


# ─────────────────────────────────────────────── nodes: place


class TestPlaceInRoom:
    def test_snaps_a_stray_node_into_its_room(self, parsed):
        parsed["nodes"][0]["point"] = [99.0, 99.0, 2.5]
        out = nodes_core.place_in_room(parsed, "office-node")
        assert out["point"] == [2.0, 1.5, 2.5]
        assert out["repointed"] is False

    def test_keeps_the_existing_height(self, parsed):
        assert nodes_core.place_in_room(parsed, "bedroom-node")["point"][2] == 2.2

    def test_explicit_z_wins(self, parsed):
        assert nodes_core.place_in_room(parsed, "office-node", z=1.2)["point"][2] == 1.2

    def test_default_height_for_a_node_without_a_point(self, parsed):
        del parsed["nodes"][0]["point"]
        assert nodes_core.place_in_room(parsed, "office-node")["point"][2] == 2.4

    def test_explicit_room_also_repoints_the_node(self, parsed):
        out = nodes_core.place_in_room(parsed, "office-node", room="Kitchen")
        assert out["repointed"] is True
        assert out["point"] == [6.0, 1.5, 2.5]
        assert parsed["nodes"][0]["room"] == "Kitchen"

    def test_passing_the_current_room_is_not_a_repoint(self, parsed):
        assert nodes_core.place_in_room(parsed, "office-node", room="Office")["repointed"] is False

    def test_resulting_point_is_inside_the_room(self, parsed):
        nodes_core.place_in_room(parsed, "office-node", room="Kitchen")
        rows = {r["room_name"]: r for r in rooms_core.geometry_report(parsed)}
        assert "office-node" in rows["Kitchen"]["nodes_inside"]
        assert rows["Kitchen"]["nodes_outside"] == []

    def test_missing_node_is_reported_not_raised(self, parsed):
        assert nodes_core.place_in_room(parsed, "ghost-node")["found"] is False

    def test_node_without_a_room_needs_an_explicit_one(self, parsed):
        parsed["nodes"].append({"name": "loose"})
        with pytest.raises(ValueError, match="no `room:`"):
            nodes_core.place_in_room(parsed, "loose")

    def test_unknown_room_raises(self, parsed):
        with pytest.raises(KeyError):
            nodes_core.place_in_room(parsed, "office-node", room="Ghost")

    def test_writes_flow_style(self, parsed):
        nodes_core.place_in_room(parsed, "office-node")
        assert "point: [2.0, 1.5, 2.5]" in yaml_io.dumps(parsed)


class TestDegradesInsteadOfCrashing:
    """Bad-but-present data must not traceback out of a read-only report."""

    def test_geometry_report_skips_non_numeric_node_points(self, parsed):
        parsed["nodes"][0]["point"] = ["a", "b", 2.5]
        rows = {r["room_name"]: r for r in rooms_core.geometry_report(parsed)}
        assert rows["Office"]["nodes_inside"] == []
        assert rows["Office"]["nodes_outside"] == []
        assert rows["Office"]["area"] == 12.0

    def test_fit_bounds_ignores_non_numeric_node_z(self, parsed):
        parsed["nodes"][2]["point"] = [1.0, 1.0, "high"]
        assert floors_core.fit_bounds(parsed, "ff")["after"][1][2] == 3.0

    def test_fit_bounds_ignores_two_dimensional_node_points(self, parsed):
        parsed["nodes"][2]["point"] = [1.0, 1.0]
        assert floors_core.fit_bounds(parsed, "ff")["after"][1][2] == 3.0

    def test_fit_bounds_ignores_a_node_with_no_point(self, parsed):
        del parsed["nodes"][2]["point"]
        assert floors_core.fit_bounds(parsed, "ff")["after"][1][2] == 3.0
