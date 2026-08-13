"""Unit tests for core/geometry.py — the pure 2D/3D maths layer.

This module has no I/O, so it is tested exhaustively and by hand-checkable
numbers rather than through the CLI. The cases that matter most are the
*negative* ones: `overlaps` must stay silent for the edge-sharing rooms that
every real floor plan is built from, and `contains_point` must not disagree
with itself on a boundary.
"""

from __future__ import annotations

import math

import pytest

from cli_anything.espresense.core import geometry as g


SQUARE = [[0, 0], [4, 0], [4, 3], [0, 3]]  # 4 x 3 = 12
L_SHAPE = [[0, 0], [4, 0], [4, 2], [2, 2], [2, 4], [0, 4]]  # 8 + 4 = 12


class TestNormalizePolygon:
    def test_returns_float_tuples(self):
        assert g.normalize_polygon(SQUARE) == [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]

    def test_drops_duplicated_closing_vertex(self):
        closed = [*SQUARE, [0, 0]]
        assert g.normalize_polygon(closed) == g.normalize_polygon(SQUARE)

    def test_accepts_tuples_and_mixed_numerics(self):
        assert g.normalize_polygon([(0, 0), (1, 0.5), (2, 1)])[1] == (1.0, 0.5)

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "0,0",
            42,
            [[0, 0], [1, 1]],  # only 2 vertices
            [[0, 0], [1, 0], [1]],  # vertex too short
            [[0, 0], [1, 0], [1, 1, 1]],  # vertex too long
            [[0, 0], [1, 0], ["a", 1]],  # non-numeric
            [[0, 0], [1, 0], 7],  # vertex not a sequence
        ],
    )
    def test_rejects_unusable_input(self, bad):
        with pytest.raises(g.GeometryError):
            g.normalize_polygon(bad)

    def test_booleans_are_not_coordinates(self):
        with pytest.raises(g.GeometryError):
            g.normalize_polygon([[0, 0], [1, 0], [True, 1]])


class TestArea:
    def test_rectangle(self):
        assert g.area(SQUARE) == pytest.approx(12.0)

    def test_l_shape(self):
        assert g.area(L_SHAPE) == pytest.approx(12.0)

    def test_winding_order_does_not_change_area(self):
        assert g.area(list(reversed(SQUARE))) == pytest.approx(g.area(SQUARE))

    def test_signed_area_flips_with_winding(self):
        assert g.signed_area(SQUARE) == pytest.approx(-g.signed_area(list(reversed(SQUARE))))

    def test_collinear_polygon_has_zero_area(self):
        assert g.area([[0, 0], [1, 0], [2, 0]]) == pytest.approx(0.0)


class TestPerimeterAndBbox:
    def test_perimeter_closes_the_ring(self):
        assert g.perimeter(SQUARE) == pytest.approx(14.0)

    def test_bbox(self):
        assert g.bbox(SQUARE) == {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 4.0,
            "max_y": 3.0,
            "width": 4.0,
            "height": 3.0,
        }


class TestCentroid:
    def test_rectangle_centroid_is_the_middle(self):
        assert g.centroid(SQUARE) == pytest.approx((2.0, 1.5))

    def test_centroid_is_winding_independent(self):
        assert g.centroid(list(reversed(SQUARE))) == pytest.approx(g.centroid(SQUARE))

    def test_l_shape_centroid_is_inside_the_l(self):
        cx, cy = g.centroid(L_SHAPE)
        assert g.contains_point(L_SHAPE, cx, cy)

    def test_degenerate_polygon_falls_back_to_vertex_mean(self):
        # No area centroid exists; callers (rooms scale) still need an origin.
        assert g.centroid([[0, 0], [2, 0], [4, 0]]) == pytest.approx((2.0, 0.0))


class TestContainsPoint:
    @pytest.mark.parametrize("pt", [(2, 1.5), (0.001, 0.001), (3.99, 2.99)])
    def test_interior_points(self, pt):
        assert g.contains_point(SQUARE, *pt) is True

    @pytest.mark.parametrize("pt", [(5, 1), (-1, 1), (2, 4), (2, -0.5)])
    def test_exterior_points(self, pt):
        assert g.contains_point(SQUARE, *pt) is False

    @pytest.mark.parametrize("pt", [(0, 0), (4, 3), (2, 0), (0, 1.5)])
    def test_boundary_counts_as_inside_by_default(self, pt):
        assert g.contains_point(SQUARE, *pt) is True
        assert g.contains_point(SQUARE, *pt, include_boundary=False) is False

    def test_concave_notch_is_outside(self):
        # (3, 3) is in the L's bounding box but in the missing corner.
        assert g.contains_point(L_SHAPE, 3, 3) is False
        assert g.contains_point(L_SHAPE, 1, 3) is True

    def test_ray_through_a_vertex_is_not_double_counted(self):
        diamond = [[0, 0], [2, 2], [4, 0], [2, -2]]
        assert g.contains_point(diamond, 2, 0) is True
        assert g.contains_point(diamond, 5, 2) is False

    def test_on_boundary_helper(self):
        assert g.on_boundary(SQUARE, 2, 0) is True
        assert g.on_boundary(SQUARE, 2, 1) is False


class TestTransforms:
    def test_translate(self):
        assert g.translate(SQUARE, 1, -2)[0] == (1.0, -2.0)

    def test_translate_preserves_area(self):
        assert g.area(g.translate(SQUARE, 10, 10)) == pytest.approx(g.area(SQUARE))

    def test_scale_about_centroid_keeps_centroid(self):
        scaled = g.scale(SQUARE, 2)
        assert g.centroid(scaled) == pytest.approx(g.centroid(SQUARE))
        assert g.area(scaled) == pytest.approx(4 * g.area(SQUARE))

    def test_scale_about_explicit_origin(self):
        scaled = g.scale(SQUARE, 2, origin=(0, 0))
        assert scaled[0] == (0.0, 0.0)
        assert scaled[2] == (8.0, 6.0)

    def test_shrinking_reduces_area_quadratically(self):
        assert g.area(g.scale(SQUARE, 0.5)) == pytest.approx(g.area(SQUARE) / 4)

    def test_zero_factor_is_rejected(self):
        with pytest.raises(g.GeometryError):
            g.scale(SQUARE, 0)


class TestOverlaps:
    def test_rooms_sharing_a_wall_do_not_overlap(self):
        # The single most important negative: every neighbouring pair of rooms
        # on a real floor plan shares an edge.
        assert g.overlaps(SQUARE, [[4, 0], [8, 0], [8, 3], [4, 3]]) is False

    def test_rooms_touching_at_a_corner_do_not_overlap(self):
        assert g.overlaps(SQUARE, [[4, 3], [8, 3], [8, 6], [4, 6]]) is False

    def test_disjoint_rooms_do_not_overlap(self):
        assert g.overlaps(SQUARE, [[10, 10], [12, 10], [12, 12], [10, 12]]) is False

    def test_half_overlapping_rectangles_are_detected(self):
        # Same y range, x ranges 0-4 and 2-6: the overlap is real but every
        # corner of each rectangle lands exactly on the other's outline, so
        # only edge-midpoint probing catches it.
        assert g.overlaps(SQUARE, [[2, 0], [6, 0], [6, 3], [2, 3]]) is True

    def test_partial_overlap_is_detected(self):
        assert g.overlaps(SQUARE, [[3, 1], [7, 1], [7, 2], [3, 2]]) is True

    def test_identical_polygons_overlap(self):
        # Every vertex is only *on* the other boundary, so this is caught by
        # the centroid signal rather than vertex containment.
        assert g.overlaps(SQUARE, SQUARE) is True

    def test_fully_contained_room_overlaps(self):
        assert g.overlaps(SQUARE, [[1, 1], [2, 1], [2, 2], [1, 2]]) is True

    def test_crossing_polygons_with_no_vertex_inside(self):
        # A plus-sign: neither polygon has a vertex inside the other.
        horizontal = [[0, 1], [4, 1], [4, 2], [0, 2]]
        vertical = [[1, 0], [2, 0], [2, 4], [1, 4]]
        assert g.overlaps(horizontal, vertical) is True

    def test_is_symmetric(self):
        a, b = SQUARE, [[3, 1], [7, 1], [7, 2], [3, 2]]
        assert g.overlaps(a, b) == g.overlaps(b, a)

    def test_probes_include_vertices_and_midpoints(self):
        probes = g._probes(g.normalize_polygon(SQUARE))
        assert (0.0, 0.0) in probes
        assert (2.0, 0.0) in probes  # midpoint of the bottom edge
        assert len(probes) == 8

    def test_bboxes_overlap_gate(self):
        assert g.bboxes_overlap(SQUARE, [[4, 0], [8, 0], [8, 3], [4, 3]]) is False
        assert g.bboxes_overlap(SQUARE, [[3, 0], [8, 0], [8, 3], [3, 3]]) is True


class TestSegmentsCross:
    def test_proper_crossing(self):
        assert g.segments_cross((0, 0), (2, 2), (0, 2), (2, 0)) is True

    def test_shared_endpoint_is_not_a_crossing(self):
        assert g.segments_cross((0, 0), (2, 2), (2, 2), (4, 0)) is False

    def test_collinear_overlap_is_not_a_crossing(self):
        assert g.segments_cross((0, 0), (2, 0), (1, 0), (3, 0)) is False

    def test_parallel_segments(self):
        assert g.segments_cross((0, 0), (2, 0), (0, 1), (2, 1)) is False

    def test_t_junction_touch_is_not_a_crossing(self):
        assert g.segments_cross((0, 0), (2, 0), (1, 0), (1, 2)) is False


class TestBounds:
    def test_normalize_sorts_corners_per_axis(self):
        lo, hi = g.normalize_bounds([[10, 10, 3], [0, 0, 0]])
        assert lo == (0.0, 0.0, 0.0)
        assert hi == (10.0, 10.0, 3.0)

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "0,0,0",
            [[0, 0, 0]],
            [[0, 0, 0], [1, 1, 1], [2, 2, 2]],
            [[0, 0], [1, 1]],
            [[0, 0, 0], "x"],
            [[0, 0, 0], [1, 1, "z"]],
        ],
    )
    def test_rejects_unusable_bounds(self, bad):
        with pytest.raises(g.GeometryError):
            g.normalize_bounds(bad)

    def test_point_in_bounds(self):
        b = [[0, 0, 0], [10, 10, 3]]
        assert g.point_in_bounds(b, 5, 5, 1.5) is True
        assert g.point_in_bounds(b, 5, 5, 4) is False
        assert g.point_in_bounds(b, 11, 5, 1) is False
        assert g.point_in_bounds(b, 5, -1, 1) is False

    def test_z_is_optional(self):
        assert g.point_in_bounds([[0, 0, 0], [10, 10, 3]], 5, 5) is True

    def test_boundary_is_inside(self):
        assert g.point_in_bounds([[0, 0, 0], [10, 10, 3]], 0, 10, 3) is True

    def test_polygon_in_bounds(self):
        assert g.polygon_in_bounds(SQUARE, [[0, 0, 0], [10, 10, 3]]) is True
        assert g.polygon_in_bounds(SQUARE, [[0, 0, 0], [2, 2, 3]]) is False

    def test_bounds_from_polygons_with_margin(self):
        assert g.bounds_from_polygons([SQUARE], margin=0.5, z_min=0, z_max=2.4) == [
            [-0.5, -0.5, 0.0],
            [4.5, 3.5, 2.4],
        ]

    def test_bounds_from_polygons_spans_every_room(self):
        other = [[6, 6], [8, 6], [8, 9], [6, 9]]
        assert g.bounds_from_polygons([SQUARE, other]) == [[0.0, 0.0, 0.0], [8.0, 9.0, 3.0]]

    def test_bounds_from_polygons_sorts_z(self):
        lo, hi = g.bounds_from_polygons([SQUARE], z_min=3, z_max=0)
        assert (lo[2], hi[2]) == (0.0, 3.0)

    def test_bounds_from_no_polygons_is_an_error(self):
        with pytest.raises(g.GeometryError):
            g.bounds_from_polygons([])


class TestNumericRobustness:
    def test_scale_round_trip_is_stable(self):
        pts = g.scale(g.scale(SQUARE, 3), 1 / 3)
        for got, want in zip(pts, g.normalize_polygon(SQUARE), strict=True):
            assert got[0] == pytest.approx(want[0])
            assert got[1] == pytest.approx(want[1])

    def test_eps_absorbs_float_noise_on_boundary(self):
        moved = g.translate(SQUARE, 0.1, 0.1)
        # 0 + 0.1 is not exactly 0.1 in binary floating point.
        assert g.contains_point(moved, 0.1, 0.1) is True

    def test_distance_helper_matches_math_dist(self):
        cx, cy = g.centroid(SQUARE)
        assert math.dist((0, 0), (cx, cy)) == pytest.approx(2.5)


class TestNonNumericCoordinatesRaiseGeometryError:
    """Callers skip rows on GeometryError; a bare ValueError would crash them.

    A config can contain `point: ["a", 1, 2]` — `config doctor` reports it as
    bad_node_point, but `rooms geometry` still walks the same data and must
    degrade rather than traceback.
    """

    def test_contains_point_rejects_a_string(self):
        with pytest.raises(g.GeometryError):
            g.contains_point(SQUARE, "a", 1)

    def test_contains_point_rejects_a_numeric_string(self):
        with pytest.raises(g.GeometryError):
            g.contains_point(SQUARE, "1.5", 1)

    def test_contains_point_rejects_a_bool(self):
        with pytest.raises(g.GeometryError):
            g.contains_point(SQUARE, True, 1)

    def test_on_boundary_rejects_a_string(self):
        with pytest.raises(g.GeometryError):
            g.on_boundary(SQUARE, "a", 1)

    def test_point_in_bounds_rejects_a_string(self):
        with pytest.raises(g.GeometryError):
            g.point_in_bounds([[0, 0, 0], [1, 1, 1]], 0.5, 0.5, "z")


class TestOverlapsFalseWithOverlappingBoxes:
    def test_square_in_the_notch_of_an_l_shape(self):
        # Bounding boxes overlap heavily, the polygons do not touch at all —
        # this is the case that must fall through every signal to False.
        notch = [[2.5, 2.5], [3.5, 2.5], [3.5, 3.5], [2.5, 3.5]]
        assert g.bboxes_overlap(L_SHAPE, notch) is True
        assert g.overlaps(L_SHAPE, notch) is False

    def test_and_it_overlaps_once_it_pokes_into_the_l(self):
        poking = [[1.5, 2.5], [3.5, 2.5], [3.5, 3.5], [1.5, 3.5]]
        assert g.overlaps(L_SHAPE, poking) is True


class TestOverlapsAsymmetricCentroidSignal:
    """The centroid probe is one-directional on purpose — see `overlaps`."""

    def test_identical_polygons_still_detected_in_both_argument_orders(self):
        a = [[0, 0], [4, 0], [4, 3], [0, 3]]
        b = [[0, 0], [4, 0], [4, 3], [0, 3]]
        assert g.overlaps(a, b) is True
        assert g.overlaps(b, a) is True

    def test_containment_is_caught_by_probes_in_both_orders(self):
        inner = [[1, 1], [2, 1], [2, 2], [1, 2]]
        assert g.overlaps(SQUARE, inner) is True
        assert g.overlaps(inner, SQUARE) is True

    def test_c_shaped_room_around_a_small_room_is_not_an_overlap(self):
        # The C's centroid sits in its own hollow, where the small room lives.
        # Testing that centroid against the small room would report a phantom
        # overlap; the small room is genuinely outside the C.
        c_shape = [[0, 0], [6, 0], [6, 2], [2, 2], [2, 4], [6, 4], [6, 6], [0, 6]]
        hollow = [[3, 2.5], [5, 2.5], [5, 3.5], [3, 3.5]]
        assert g.bboxes_overlap(c_shape, hollow) is True
        assert g.overlaps(c_shape, hollow) is False
        assert g.overlaps(hollow, c_shape) is False
