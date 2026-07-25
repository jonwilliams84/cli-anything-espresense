"""Regression tests: verify B101 assert_used fixes in test_core.py are behaviour-preserving.

Each test reproduces the exact condition previously guarded by an `assert` on
lines 123, 124, and 140 of test_core.py.  All three were replaced with
pytest.fail() to ensure the checks survive `python -O`.
"""

import pytest

from cli_anything.espresense.core import rooms as rooms_core
from cli_anything.espresense.utils import yaml_io
from cli_anything.espresense.tests.test_core import SAMPLE


class TestB101Regression:
    """Regression suite for B101 assert_used fixes in test_core.py."""

    def test_regression_rename_noop_rooms_renamed_is_zero(self):
        """Formerly test_core.py:123 — assert summary["rooms_renamed"] == 0."""
        parsed = yaml_io.load(SAMPLE)
        summary = rooms_core.rename(parsed, "Spare Room", "Spare Room")
        # B101 fix: was bare assert; replaced with pytest.fail() on line 123.
        # The check must still fail when rooms_renamed != 0.
        if summary["rooms_renamed"] != 0:
            pytest.fail(
                f"Expected rooms_renamed == 0 for no-op rename, got {summary['rooms_renamed']}"
            )

    def test_regression_rename_noop_nodes_repointed_is_zero(self):
        """Formerly test_core.py:124 — assert summary["nodes_repointed"] == 0."""
        parsed = yaml_io.load(SAMPLE)
        summary = rooms_core.rename(parsed, "Spare Room", "Spare Room")
        # B101 fix: was bare assert; replaced with pytest.fail() on line 124.
        # The check must still fail when nodes_repointed != 0.
        if summary["nodes_repointed"] != 0:
            pytest.fail(
                f"Expected nodes_repointed == 0 for no-op rename, got {summary['nodes_repointed']}"
            )

    def test_regression_rotate_three_way_sorted_names_match(self):
        """Formerly test_core.py:140 — assert sorted(names) == sorted([...]).

        Verifies that after a three-way room rotation (Noah→Sophie→Spare→Noah),
        the final set of room names is exactly the rotated set.
        """
        parsed = yaml_io.load(SAMPLE)
        # Three-way rotation: Spare→Noah, Noah→Sophie, Sophie→Spare
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        names = [r["name"] for r in parsed["floors"][1]["rooms"]]
        expected = sorted(["Noah Bedroom", "Sophie Bedroom", "Spare Room", "Master Bedroom"])
        # B101 fix: was bare assert; replaced with pytest.fail() on line 140.
        if sorted(names) != expected:
            pytest.fail(f"Expected rotated room names {expected}, got {sorted(names)}")

    def test_regression_rotate_three_way_room_0_is_noah(self):
        """Formerly test_core.py:145 — assert rooms[0].name == 'Noah Bedroom'.

        Verifies that after the three-way rotation, the first room (originally
        'Spare Room') has been renamed to 'Noah Bedroom'.
        """
        parsed = yaml_io.load(SAMPLE)
        # Three-way rotation: Spare→Noah, Noah→Sophie, Sophie→Spare
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        # B101 fix: was bare assert; replaced with pytest.fail() on line 145.
        actual = parsed["floors"][1]["rooms"][0]["name"]
        if actual != "Noah Bedroom":
            pytest.fail(f"Expected rooms[0].name == 'Noah Bedroom', got {actual!r}")

    def test_regression_rotate_three_way_room_1_is_sophie(self):
        """Formerly test_core.py:146 — assert rooms[1].name == 'Sophie Bedroom'.

        Verifies that after the three-way rotation, the second room (originally
        'Noah Bedroom') has been renamed to 'Sophie Bedroom'.
        """
        parsed = yaml_io.load(SAMPLE)
        # Three-way rotation: Spare→Noah, Noah→Sophie, Sophie→Spare
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        # B101 fix: was bare assert; replaced with pytest.fail() on line 146.
        actual = parsed["floors"][1]["rooms"][1]["name"]
        if actual != "Sophie Bedroom":
            pytest.fail(f"Expected rooms[1].name == 'Sophie Bedroom', got {actual!r}")

    def test_regression_rotate_three_way_room_2_is_spare(self):
        """Formerly test_core.py:147 — assert rooms[2].name == 'Spare Room'.

        Verifies that after the three-way rotation, the third room (originally
        'Sophie Bedroom') has been renamed back to 'Spare Room'.
        """
        parsed = yaml_io.load(SAMPLE)
        # Three-way rotation: Spare→Noah, Noah→Sophie, Sophie→Spare
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        # B101 fix: was bare assert; replaced with pytest.fail() on line 147.
        actual = parsed["floors"][1]["rooms"][2]["name"]
        if actual != "Spare Room":
            pytest.fail(f"Expected rooms[2].name == 'Spare Room', got {actual!r}")


class TestB101RotateNodeRoomRegression:
    """Regression suite for B101 assert_used fixes at test_core.py lines 157/159/161.

    Each test reproduces the exact condition previously guarded by a bare
    `assert`.  Replacing `assert` → `pytest.fail` preserves behaviour under
    `python -O` (optimised bytecode removes bare asserts).
    """

    def test_regression_rotate_n_noah_room_is_spare(self):
        """Formerly test_core.py:157 — assert n_noah['room'] == 'Spare Room'."""
        parsed = yaml_io.load(SAMPLE)
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        n_noah = next(n for n in parsed["nodes"] if n["name"] == "noah-bedroom")
        # B101 fix: was bare assert; now pytest.fail so check survives -O
        if n_noah["room"] != "Spare Room":
            pytest.fail(f"Expected n_noah['room'] == 'Spare Room', got {n_noah['room']!r}")

    def test_regression_rotate_n_sophie_room_is_noah(self):
        """Formerly test_core.py:159 — assert n_sophie['room'] == 'Noah Bedroom'."""
        parsed = yaml_io.load(SAMPLE)
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        n_sophie = next(n for n in parsed["nodes"] if n["name"] == "sophie-bedroom")
        # B101 fix: was bare assert; now pytest.fail so check survives -O
        if n_sophie["room"] != "Noah Bedroom":
            pytest.fail(f"Expected n_sophie['room'] == 'Noah Bedroom', got {n_sophie['room']!r}")

    def test_regression_rotate_n_spare_room_is_sophie(self):
        """Formerly test_core.py:161 — assert n_spare['room'] == 'Sophie Bedroom'."""
        parsed = yaml_io.load(SAMPLE)
        rooms_core.rotate(
            parsed,
            {
                "Spare Room": "Noah Bedroom",
                "Noah Bedroom": "Sophie Bedroom",
                "Sophie Bedroom": "Spare Room",
            },
        )
        n_spare = next(n for n in parsed["nodes"] if n["name"] == "spare-room")
        # B101 fix: was bare assert; now pytest.fail so check survives -O
        if n_spare["room"] != "Sophie Bedroom":
            pytest.fail(f"Expected n_spare['room'] == 'Sophie Bedroom', got {n_spare['room']!r}")
