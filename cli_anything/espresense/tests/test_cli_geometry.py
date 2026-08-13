"""End-to-end CLI tests for the floor-plan geometry refine pass.

`floors add/rename/retag/set-bounds/fit-bounds/delete`, the `rooms` geometry
commands (`geometry`, `locate`, `overlaps`, `set-points`, `move`, `scale`,
`set-color`) and `nodes place` all edit or read config.yaml, so every test here
runs the real command against a real YAML file via `--file`. Nothing is mocked:
the assertions are about the process exit code, the `--json` payload, and the
bytes left on disk.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything.espresense.espresense_cli import cli


CONFIG_YAML = """\
# ESPresense companion config
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
def env(tmp_path, monkeypatch):
    """A CliRunner bound to an isolated profile and a real config.yaml."""
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    monkeypatch.setattr("cli_anything.espresense.core.project.DEFAULT_CONFIG_PATH", profile)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML, encoding="utf-8")

    def run(*args, json_out=True):
        argv = ["--config", str(profile)]
        if json_out:
            argv.append("--json")
        argv.extend([*args, "--file", str(cfg)])
        return CliRunner().invoke(cli, argv)

    run.cfg = cfg  # type: ignore[attr-defined]
    return run


def payload(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def reread(env, *args):
    """Re-run a read-only command to prove an edit really landed on disk."""
    return payload(env(*args))


# ─────────────────────────────────────────────── floors add / rename / retag


class TestFloorsAdd:
    def test_adds_a_floor_and_writes_it(self, env):
        data = payload(env("floors", "add", "bs", "--name", "Basement"))
        assert data["added"] is True
        assert data["pushed"]["source"] == "file"
        assert [f["id"] for f in reread(env, "floors", "list")] == ["gf", "ff", "bs"]

    def test_bounds_option(self, env):
        data = payload(env("floors", "add", "bs", "--bounds", "0,0,0 5,4,2.4"))
        assert data["bounds"] == [[0.0, 0.0, 0.0], [5.0, 4.0, 2.4]]
        assert "bounds: [[0.0, 0.0, 0.0], [5.0, 4.0, 2.4]]" in env.cfg.read_text()

    def test_bad_bounds_arity_is_rejected(self, env):
        res = env("floors", "add", "bs", "--bounds", "0,0,0")
        assert res.exit_code == 1
        assert "two corners" in res.output

    def test_non_numeric_bounds_are_rejected(self, env):
        res = env("floors", "add", "bs", "--bounds", "0,0,0 a,b,c")
        assert res.exit_code == 1
        assert "must be numbers" in res.output

    def test_duplicate_id_is_refused(self, env):
        res = env("floors", "add", "gf")
        assert res.exit_code == 1
        assert "already exists" in res.output

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        data = payload(env("floors", "add", "bs", "--dry-run"))
        assert data["dry_run"] is True
        assert "pushed" not in data
        assert env.cfg.read_text() == before

    def test_new_floor_can_immediately_take_a_room(self, env):
        payload(env("floors", "add", "bs", "--name", "Basement"))
        payload(
            env(
                "rooms",
                "add",
                "bs",
                "Cellar",
                "--point",
                "0,0",
                "--point",
                "3,0",
                "--point",
                "3,3",
                "--point",
                "0,3",
            )
        )
        rooms = reread(env, "rooms", "list", "--floor", "bs")
        assert [r["room_name"] for r in rooms] == ["Cellar"]


class TestFloorsRename:
    def test_renames_the_display_name(self, env):
        data = payload(env("floors", "rename", "gf", "Downstairs"))
        assert data["before"] == "Ground Floor"
        assert reread(env, "floors", "show", "gf")["name"] == "Downstairs"

    def test_unknown_floor_exits_one(self, env):
        res = env("floors", "rename", "attic", "Attic")
        assert res.exit_code == 1
        assert "no floor" in res.output


class TestFloorsRetag:
    def test_retag_updates_node_refs_too(self, env):
        data = payload(env("floors", "retag", "gf", "ground"))
        assert data["nodes_repointed"] == 1
        assert [f["id"] for f in reread(env, "floors", "list")] == ["ground", "ff"]

    def test_retagged_config_stays_clean(self, env):
        payload(env("floors", "retag", "gf", "ground"))
        assert payload(env("config", "doctor"))["errors"] == []

    def test_noop_retag_does_not_push(self, env):
        data = payload(env("floors", "retag", "gf", "gf"))
        assert data["id_changed"] is False
        assert "pushed" not in data

    def test_collision_is_refused(self, env):
        res = env("floors", "retag", "gf", "ff")
        assert res.exit_code == 1
        assert "already exists" in res.output

    def test_unknown_floor_exits_one(self, env):
        assert env("floors", "retag", "attic", "loft").exit_code == 1


# ─────────────────────────────────────────────── floors bounds


class TestFloorsSetBounds:
    def test_sets_bounds(self, env):
        data = payload(env("floors", "set-bounds", "gf", "0,0,0", "12,9,2.4"))
        assert data["after"] == [[0.0, 0.0, 0.0], [12.0, 9.0, 2.4]]
        assert reread(env, "floors", "show", "gf")["bounds"] == [[0.0, 0.0, 0.0], [12.0, 9.0, 2.4]]

    def test_corners_are_sorted_per_axis(self, env):
        data = payload(env("floors", "set-bounds", "gf", "12,9,2.4", "0,0,0"))
        assert data["after"] == [[0.0, 0.0, 0.0], [12.0, 9.0, 2.4]]

    def test_bad_corner_is_rejected(self, env):
        res = env("floors", "set-bounds", "gf", "0,0", "1,1,1")
        assert res.exit_code == 1
        assert "expected x,y,z" in res.output

    def test_unknown_floor_exits_one(self, env):
        assert env("floors", "set-bounds", "attic", "0,0,0", "1,1,1").exit_code == 1

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("floors", "set-bounds", "gf", "0,0,0", "1,1,1", "--dry-run"))
        assert env.cfg.read_text() == before


class TestFloorsFitBounds:
    def test_fits_to_the_rooms(self, env):
        data = payload(env("floors", "fit-bounds", "gf"))
        assert data["after"] == [[0.0, 0.0, 0.0], [8.0, 3.0, 3.0]]
        assert data["rooms_considered"] == 2

    def test_margin(self, env):
        assert payload(env("floors", "fit-bounds", "gf", "--margin", "0.5"))["after"][1][0] == 8.5

    def test_z_overrides(self, env):
        data = payload(env("floors", "fit-bounds", "gf", "--z-min", "0.1", "--z-max", "2.2"))
        assert (data["after"][0][2], data["after"][1][2]) == (0.1, 2.2)

    def test_floor_without_rooms_exits_one(self, env):
        payload(env("floors", "add", "bs"))
        res = env("floors", "fit-bounds", "bs")
        assert res.exit_code == 1
        assert "no room polygons" in res.output

    def test_unknown_floor_exits_one(self, env):
        assert env("floors", "fit-bounds", "attic").exit_code == 1

    def test_fit_clears_a_bounds_warning(self, env):
        payload(env("floors", "set-bounds", "gf", "0,0,0", "5,5,3"))
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "room_outside_floor_bounds" in codes
        payload(env("floors", "fit-bounds", "gf"))
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "room_outside_floor_bounds" not in codes


# ─────────────────────────────────────────────── floors delete


class TestFloorsDelete:
    def test_refuses_while_nodes_are_attached(self, env):
        res = env("floors", "delete", "ff")
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["pushed"] is None
        assert "bedroom-node" in data["refused"]
        assert "First Floor" in env.cfg.read_text()

    def test_force_deletes_and_reports_the_fallout(self, env):
        data = payload(env("floors", "delete", "ff", "--force"))
        assert data["rooms_removed"] == 1
        assert data["orphaned_nodes"] == ["bedroom-node"]
        assert [f["id"] for f in reread(env, "floors", "list")] == ["gf"]

    def test_deleting_an_empty_floor_needs_no_force(self, env):
        payload(env("floors", "add", "bs"))
        assert payload(env("floors", "delete", "bs"))["deleted"] is True

    def test_unknown_floor_exits_one(self, env):
        res = env("floors", "delete", "attic")
        assert res.exit_code == 1
        assert "no floor" in res.output

    def test_dry_run_with_force_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("floors", "delete", "ff", "--force", "--dry-run"))
        assert env.cfg.read_text() == before

    def test_orphans_are_then_visible_to_doctor(self, env):
        payload(env("floors", "delete", "ff", "--force"))
        res = env("config", "doctor")
        assert res.exit_code == 1
        assert "dangling_room_ref" in {e["code"] for e in json.loads(res.output)["errors"]}


# ─────────────────────────────────────────────── rooms geometry (read-only)


class TestRoomsGeometry:
    def test_reports_area_and_centroid(self, env):
        rows = payload(env("rooms", "geometry"))
        office = next(r for r in rows if r["room_name"] == "Office")
        assert office["area"] == 12.0
        assert office["centroid"] == [2.0, 1.5]
        assert office["nodes_inside"] == ["office-node"]

    def test_floor_filter(self, env):
        rows = payload(env("rooms", "geometry", "--floor", "ff"))
        assert [r["room_name"] for r in rows] == ["Bedroom"]

    def test_flags_a_node_outside_its_room(self, env):
        payload(env("nodes", "set-point", "office-node", "99", "99", "2.5"))
        office = next(r for r in payload(env("rooms", "geometry")) if r["room_name"] == "Office")
        assert office["nodes_outside"] == ["office-node"]

    def test_human_readable_table(self, env):
        res = env("rooms", "geometry", json_out=False)
        assert res.exit_code == 0
        assert "room_name" in res.output
        assert "Office" in res.output

    def test_is_read_only(self, env):
        before = env.cfg.read_text()
        env("rooms", "geometry")
        assert env.cfg.read_text() == before


class TestRoomsLocate:
    def test_finds_the_room_for_a_point(self, env):
        hits = payload(env("rooms", "locate", "5", "1", "--floor", "gf"))
        assert [h["room_name"] for h in hits] == ["Kitchen"]

    def test_point_in_no_room_exits_one(self, env):
        res = env("rooms", "locate", "50", "50")
        assert res.exit_code == 1
        assert json.loads(res.output) == []

    def test_human_readable_miss_explains_itself(self, env):
        res = env("rooms", "locate", "50", "50", json_out=False)
        assert res.exit_code == 1
        assert "not inside any room" in res.output

    def test_reports_every_floor_by_default(self, env):
        hits = payload(env("rooms", "locate", "1", "1"))
        assert {h["room_name"] for h in hits} == {"Office", "Bedroom"}

    def test_is_read_only(self, env):
        before = env.cfg.read_text()
        env("rooms", "locate", "1", "1")
        assert env.cfg.read_text() == before


class TestRoomsOverlaps:
    def test_clean_plan_reports_nothing_and_exits_zero(self, env):
        res = env("rooms", "overlaps")
        assert res.exit_code == 0
        assert json.loads(res.output) == []

    def test_human_readable_clean_message(self, env):
        res = env("rooms", "overlaps", json_out=False)
        assert res.exit_code == 0
        assert "no overlapping rooms" in res.output

    def test_overlap_exits_one_so_it_can_gate(self, env):
        payload(env("rooms", "move", "Kitchen", "-2", "0"))
        res = env("rooms", "overlaps")
        assert res.exit_code == 1
        pairs = json.loads(res.output)
        assert {pairs[0]["room_a"], pairs[0]["room_b"]} == {"Office", "Kitchen"}

    def test_floor_filter(self, env):
        payload(env("rooms", "move", "Kitchen", "-2", "0"))
        assert env("rooms", "overlaps", "--floor", "ff").exit_code == 0


# ─────────────────────────────────────────────── rooms polygon edits


class TestRoomsSetPoints:
    def test_replaces_the_polygon(self, env):
        data = payload(
            env(
                "rooms",
                "set-points",
                "Office",
                "--point",
                "0,0",
                "--point",
                "5,0",
                "--point",
                "5,4",
                "--point",
                "0,4",
            )
        )
        assert data["point_count"] == 4
        office = next(r for r in payload(env("rooms", "geometry")) if r["room_name"] == "Office")
        assert office["area"] == 20.0

    def test_stays_flow_styled_on_disk(self, env):
        payload(
            env(
                "rooms",
                "set-points",
                "Office",
                "--point",
                "0,0",
                "--point",
                "5,0",
                "--point",
                "5,4",
                "--point",
                "0,4",
            )
        )
        assert "points: [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]" in env.cfg.read_text()

    def test_too_few_points_exits_one(self, env):
        res = env("rooms", "set-points", "Office", "--point", "0,0", "--point", "1,1")
        assert res.exit_code == 1
        assert "at least 3" in res.output

    def test_unknown_room_exits_one(self, env):
        res = env(
            "rooms",
            "set-points",
            "Ghost",
            "--point",
            "0,0",
            "--point",
            "1,0",
            "--point",
            "1,1",
        )
        assert res.exit_code == 1
        assert "no room named" in res.output

    def test_malformed_point_exits_one(self, env):
        res = env("rooms", "set-points", "Office", "--point", "0,0,0")
        assert res.exit_code == 1
        assert "expected x,y" in res.output


class TestRoomsMove:
    def test_translates_the_room(self, env):
        data = payload(env("rooms", "move", "Office", "1", "2"))
        assert data["after"][0] == [1.0, 2.0]
        assert "points: [[1.0, 2.0], [5.0, 2.0], [5.0, 5.0], [1.0, 5.0]]" in env.cfg.read_text()

    def test_negative_deltas(self, env):
        assert payload(env("rooms", "move", "Office", "-1", "-1"))["after"][0] == [-1.0, -1.0]

    def test_unknown_room_exits_one(self, env):
        assert env("rooms", "move", "Ghost", "1", "1").exit_code == 1

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("rooms", "move", "Office", "1", "1", "--dry-run"))
        assert env.cfg.read_text() == before

    def test_moving_a_room_can_strand_its_node(self, env):
        payload(env("rooms", "move", "Office", "20", "20"))
        office = next(r for r in payload(env("rooms", "geometry")) if r["room_name"] == "Office")
        assert office["nodes_outside"] == ["office-node"]
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "node_point_outside_room" in codes


class TestRoomsScale:
    def test_scales_about_the_centroid_by_default(self, env):
        data = payload(env("rooms", "scale", "Office", "2"))
        assert data["about"] == "centroid"
        assert data["area_after"] == 48.0

    def test_about_origin(self, env):
        data = payload(env("rooms", "scale", "Office", "2", "--about-origin"))
        assert data["about"] == "origin"
        assert data["after"][2] == [8.0, 6.0]

    def test_shrinking(self, env):
        assert payload(env("rooms", "scale", "Office", "0.5"))["area_after"] == 3.0

    def test_zero_factor_exits_one(self, env):
        res = env("rooms", "scale", "Office", "0")
        assert res.exit_code == 1
        assert "non-zero" in res.output

    def test_unknown_room_exits_one(self, env):
        assert env("rooms", "scale", "Ghost", "2").exit_code == 1


class TestRoomsSetColor:
    def test_sets_a_colour(self, env):
        data = payload(env("rooms", "set-color", "Office", "#a3c9f9"))
        assert data["after"] == "#a3c9f9"
        assert "color: '#a3c9f9'" in env.cfg.read_text()

    def test_clear_removes_it(self, env):
        payload(env("rooms", "set-color", "Office", "#a3c9f9"))
        data = payload(env("rooms", "set-color", "Office", "--clear"))
        assert data["after"] is None
        assert "#a3c9f9" not in env.cfg.read_text()

    def test_color_and_clear_together_are_refused(self, env):
        res = env("rooms", "set-color", "Office", "#fff", "--clear")
        assert res.exit_code == 1
        assert "not both" in res.output

    def test_missing_colour_is_refused(self, env):
        res = env("rooms", "set-color", "Office")
        assert res.exit_code == 1
        assert "COLOR is required" in res.output

    def test_unknown_room_exits_one(self, env):
        assert env("rooms", "set-color", "Ghost", "#fff").exit_code == 1

    def test_colour_shows_up_in_rooms_list(self, env):
        payload(env("rooms", "set-color", "Office", "#a3c9f9"))
        office = next(r for r in reread(env, "rooms", "list") if r["room_name"] == "Office")
        assert office["has_color"] is True


# ─────────────────────────────────────────────── nodes place


class TestNodesPlace:
    def test_snaps_a_node_to_its_room_centroid(self, env):
        payload(env("nodes", "set-point", "office-node", "99", "99", "2.5"))
        data = payload(env("nodes", "place", "office-node"))
        assert data["point"] == [2.0, 1.5, 2.5]
        assert data["repointed"] is False

    def test_placement_clears_the_doctor_warning(self, env):
        payload(env("nodes", "set-point", "office-node", "99", "99", "2.5"))
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "node_point_outside_room" in codes
        payload(env("nodes", "place", "office-node"))
        codes = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert "node_point_outside_room" not in codes

    def test_room_option_also_repoints(self, env):
        data = payload(env("nodes", "place", "office-node", "--room", "Kitchen"))
        assert data["repointed"] is True
        node = next(
            n for n in reread(env, "nodes", "list", "--no-merge-live") if n["name"] == "office-node"
        )
        assert node["room"] == "Kitchen"
        assert node["point"] == [6.0, 1.5, 2.5]

    def test_z_option(self, env):
        assert payload(env("nodes", "place", "office-node", "--z", "1.2"))["point"][2] == 1.2

    def test_unknown_node_exits_one(self, env):
        res = env("nodes", "place", "ghost-node")
        assert res.exit_code == 1
        assert "no node named" in res.output

    def test_unknown_room_exits_one(self, env):
        res = env("nodes", "place", "office-node", "--room", "Ghost")
        assert res.exit_code == 1
        assert "no room named" in res.output

    def test_dry_run_does_not_write(self, env):
        before = env.cfg.read_text()
        payload(env("nodes", "place", "office-node", "--room", "Kitchen", "--dry-run"))
        assert env.cfg.read_text() == before


# ─────────────────────────────────────────────── multi-command workflows


class TestGeometryWorkflows:
    def test_build_a_floor_from_nothing(self, env):
        """floors add -> rooms add -> nodes add -> nodes place -> fit-bounds -> doctor."""
        payload(env("floors", "add", "bs", "--name", "Basement"))
        payload(
            env(
                "rooms",
                "add",
                "bs",
                "Cellar",
                "--point",
                "0,0",
                "--point",
                "3,0",
                "--point",
                "3,3",
                "--point",
                "0,3",
            )
        )
        payload(env("nodes", "add", "cellar-node", "--room", "Cellar", "--floor", "bs"))
        placed = payload(env("nodes", "place", "cellar-node"))
        assert placed["point"] == [1.5, 1.5, 2.4]
        fitted = payload(env("floors", "fit-bounds", "bs", "--margin", "0.25"))
        assert fitted["after"] == [[-0.25, -0.25, 0.0], [3.25, 3.25, 3.0]]
        report = payload(env("config", "doctor"))
        assert report["errors"] == []
        assert report["counts"]["floors"] == 3

    def test_move_a_room_then_repair_everything(self, env):
        """A move strands a node and breaks the bounds; the fixes are commands."""
        payload(env("rooms", "move", "Kitchen", "10", "10"))
        warnings = {w["code"] for w in payload(env("config", "doctor"))["warnings"]}
        assert {"node_point_outside_room", "room_outside_floor_bounds"} <= warnings
        payload(env("nodes", "place", "kitchen-node"))
        payload(env("floors", "fit-bounds", "gf"))
        assert payload(env("config", "doctor"))["warnings"] == []

    def test_overlap_gate_then_repair(self, env):
        payload(env("rooms", "move", "Kitchen", "-2", "0"))
        assert env("rooms", "overlaps").exit_code == 1
        # shrinking is not enough while the centroid still sits on Office's
        # wall, so the repair is to put the room back where it belongs
        payload(env("rooms", "scale", "Kitchen", "0.4"))
        assert env("rooms", "overlaps").exit_code == 1
        payload(env("rooms", "move", "Kitchen", "2", "0"))
        assert env("rooms", "overlaps").exit_code == 0

    def test_retire_a_floor_end_to_end(self, env):
        """delete refuses -> repoint the node -> delete succeeds -> config clean."""
        assert env("floors", "delete", "ff").exit_code == 1
        payload(env("rooms", "repoint-node", "bedroom-node", "Office"))
        payload(env("nodes", "place", "bedroom-node"))
        # the node still lists floor ff; retag-free path is to drop the floor
        # with --force and let doctor confirm what is left.
        payload(env("floors", "delete", "ff", "--force"))
        res = env("config", "doctor")
        codes = {e["code"] for e in json.loads(res.output)["errors"]}
        assert codes == {"dangling_floor_ref"}

    def test_redraw_a_room_keeps_its_node_inside(self, env):
        payload(
            env(
                "rooms",
                "set-points",
                "Office",
                "--point",
                "0,0",
                "--point",
                "6,0",
                "--point",
                "6,5",
                "--point",
                "0,5",
            )
        )
        payload(env("nodes", "place", "office-node"))
        located = payload(env("rooms", "locate", "3", "2.5", "--floor", "gf"))
        assert [h["room_name"] for h in located] == ["Office"]
        assert payload(env("config", "doctor"))["ok"] is True

    def test_every_edit_leaves_a_backup(self, env, tmp_path):
        payload(env("rooms", "move", "Office", "1", "0"))
        assert list(tmp_path.glob("config.yaml.*.bak"))

    def test_comments_survive_the_geometry_edits(self, env):
        payload(env("rooms", "move", "Office", "1", "0"))
        payload(env("floors", "fit-bounds", "gf"))
        assert "# ESPresense companion config" in env.cfg.read_text()


class TestSignedCoordinateArguments:
    """`rooms move Office -2 0` must work.

    Click reads a leading-dash token as an option, so without
    `ignore_unknown_options` on these commands the entire negative half of the
    coordinate plane is unreachable from a shell. Locked down here because it
    is invisible in a help dump.
    """

    def test_rooms_move_accepts_negative_deltas(self, env):
        assert payload(env("rooms", "move", "Office", "-1", "-2"))["after"][0] == [-1.0, -2.0]

    def test_rooms_locate_accepts_negative_coordinates(self, env):
        payload(env("rooms", "move", "Office", "-4", "-3"))
        hits = payload(env("rooms", "locate", "-2", "-1.5", "--floor", "gf"))
        assert [h["room_name"] for h in hits] == ["Office"]

    def test_floors_set_bounds_accepts_negative_corners(self, env):
        data = payload(env("floors", "set-bounds", "gf", "-5,-5,0", "5,5,3"))
        assert data["after"] == [[-5.0, -5.0, 0.0], [5.0, 5.0, 3.0]]

    def test_nodes_set_point_accepts_negative_coordinates(self, env):
        data = payload(env("nodes", "set-point", "office-node", "-1", "-2", "2.5"))
        assert data["after"] == [-1.0, -2.0, 2.5]

    def test_declared_options_still_parse_on_those_commands(self, env):
        before = env.cfg.read_text()
        assert payload(env("rooms", "move", "Office", "-1", "0", "--dry-run"))["dry_run"] is True
        assert env.cfg.read_text() == before

    def test_typo_option_is_still_rejected(self, env):
        res = env("rooms", "move", "Office", "1", "0", "--dryrun")
        assert res.exit_code != 0
