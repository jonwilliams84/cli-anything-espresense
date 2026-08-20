"""Guards that the docs keep describing the CLI that actually exists.

Two things drifted easily before: the duplicated skill manifest (a copy in
`skills/` and one inside the package, required to be byte-identical) and the
command tables in the READMEs, which quietly lag behind new command groups.
Both are cheap to assert, so they are asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.espresense.espresense_cli import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_COPIES = (
    REPO_ROOT / "skills" / "cli-anything-espresense" / "SKILL.md",
    REPO_ROOT / "cli_anything" / "espresense" / "skills" / "SKILL.md",
)
READMES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "cli_anything" / "espresense" / "README.md",
)


def _skip_if_missing(path: Path):
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")


class TestSkillManifestCopies:
    def test_both_copies_exist(self):
        for path in SKILL_COPIES:
            _skip_if_missing(path)
            assert path.is_file()

    def test_copies_are_byte_identical(self):
        for path in SKILL_COPIES:
            _skip_if_missing(path)
        a, b = (p.read_bytes() for p in SKILL_COPIES)
        assert a == b, "SKILL.md copies have drifted; they must stay identical"


class TestReadmesDocumentEveryGroup:
    @pytest.mark.parametrize("readme", READMES, ids=lambda p: p.parent.name)
    def test_every_top_level_group_is_mentioned(self, readme):
        _skip_if_missing(readme)
        text = readme.read_text(encoding="utf-8")
        missing = [name for name in cli.commands if name not in text]
        assert missing == [], f"undocumented command groups in {readme.name}: {missing}"

    @pytest.mark.parametrize("readme", READMES, ids=lambda p: p.parent.name)
    def test_the_config_side_device_commands_are_documented(self, readme):
        _skip_if_missing(readme)
        assert "add-to-config" in readme.read_text(encoding="utf-8")


class TestHelpMentionsBothDeviceHalves:
    def test_devices_help_distinguishes_runtime_from_config(self):
        res = CliRunner().invoke(cli, ["devices", "--help"])
        assert res.exit_code == 0
        assert "list-in-config" in res.output
        assert "config.yaml" in res.output

    def test_settings_help_warns_off_structural_blocks(self):
        res = CliRunner().invoke(cli, ["settings", "--help"])
        assert res.exit_code == 0
        assert "floors/rooms/nodes/devices" in res.output
