"""Unit tests for the config-source abstraction (core/config_source.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cli_anything.espresense.core import config_source, k8s_backend

SAMPLE = """\
floors:
  - id: gf
    name: Ground Floor
    rooms:
      - name: Office
        points: [[0, 0], [4, 0], [4, 3], [0, 3]]
nodes:
  - name: office-node
    room: Office
    point: [1.0, 2.0, 2.5]
"""


@pytest.fixture()
def cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


class TestFromOptions:
    def test_file_wins_when_given(self, cfg_file):
        src = config_source.from_options(k8s_backend.K8sTarget(), str(cfg_file))
        assert isinstance(src, config_source.FileSource)
        assert src.kind == "file"

    def test_defaults_to_k8s(self):
        src = config_source.from_options(k8s_backend.K8sTarget())
        assert isinstance(src, config_source.K8sSource)
        assert src.kind == "k8s"

    def test_empty_string_file_is_not_a_file_source(self):
        src = config_source.from_options(k8s_backend.K8sTarget(), "")
        assert isinstance(src, config_source.K8sSource)

    def test_expands_user_home(self):
        src = config_source.from_options(k8s_backend.K8sTarget(), "~/cfg.yaml")
        assert "~" not in str(src.path)


class TestFileSource:
    def test_fetch_round_trips(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        raw, parsed = src.fetch()
        assert raw == SAMPLE
        assert parsed["floors"][0]["id"] == "gf"
        assert parsed["nodes"][0]["name"] == "office-node"

    def test_fetch_missing_file_raises(self, tmp_path):
        src = config_source.FileSource(tmp_path / "nope.yaml")
        with pytest.raises(config_source.ConfigSourceError, match="not found"):
            src.fetch()

    def test_push_writes_and_backs_up(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        _, parsed = src.fetch()
        parsed["nodes"][0]["room"] = "Study"
        summary = src.push(parsed)
        assert summary["source"] == "file"
        assert summary["backed_up"] is True
        assert Path(summary["backup_path"]).read_text(encoding="utf-8") == SAMPLE
        assert "Study" in cfg_file.read_text(encoding="utf-8")
        assert summary["bytes_written"] > 0

    def test_push_no_backup(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        _, parsed = src.fetch()
        summary = src.push(parsed, backup=False)
        assert summary["backed_up"] is False
        assert "backup_path" not in summary
        assert not list(cfg_file.parent.glob("*.bak"))

    def test_push_preserves_comments(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text("# keep me\nnodes:\n  - name: a\n", encoding="utf-8")
        src = config_source.FileSource(p)
        _, parsed = src.fetch()
        src.push(parsed, backup=False)
        assert "# keep me" in p.read_text(encoding="utf-8")

    def test_push_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "c.yaml"
        src = config_source.FileSource(target)
        src.push({"nodes": []}, backup=False)
        assert target.exists()

    def test_restart_is_reported_as_skipped_not_silently_dropped(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        _, parsed = src.fetch()
        summary = src.push(parsed, restart=True)
        assert summary["restarted"] is False
        assert "restart_skipped" in summary

    def test_backup_skipped_for_new_file(self, tmp_path):
        src = config_source.FileSource(tmp_path / "new.yaml")
        summary = src.push({"nodes": []}, backup=True)
        assert summary["backed_up"] is False

    def test_describe(self, cfg_file):
        assert config_source.FileSource(cfg_file).describe().startswith("file://")

    def test_fetch_read_error_wrapped(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        with patch.object(Path, "read_text", side_effect=OSError("boom")):
            with pytest.raises(config_source.ConfigSourceError, match="cannot read"):
                src.fetch()

    def test_push_write_error_wrapped(self, cfg_file):
        src = config_source.FileSource(cfg_file)
        with patch.object(Path, "write_text", side_effect=OSError("full")):
            with pytest.raises(config_source.ConfigSourceError, match="cannot write"):
                src.push({"nodes": []}, backup=False)


class TestK8sSource:
    def test_fetch_uses_kubectl_read(self):
        src = config_source.K8sSource(k8s_backend.K8sTarget())
        with patch.object(k8s_backend, "read_config", return_value=SAMPLE) as m:
            raw, parsed = src.fetch()
        m.assert_called_once()
        assert raw == SAMPLE
        assert parsed["floors"][0]["id"] == "gf"

    def test_push_writes_and_does_not_restart_by_default(self):
        src = config_source.K8sSource(k8s_backend.K8sTarget())
        with (
            patch.object(k8s_backend, "write_config") as w,
            patch.object(k8s_backend, "restart") as r,
        ):
            summary = src.push({"nodes": []})
        w.assert_called_once()
        r.assert_not_called()
        assert summary["source"] == "k8s"
        assert summary["restarted"] is False

    def test_push_restarts_when_asked(self):
        src = config_source.K8sSource(k8s_backend.K8sTarget())
        with (
            patch.object(k8s_backend, "write_config"),
            patch.object(k8s_backend, "restart") as r,
        ):
            summary = src.push({"nodes": []}, restart=True)
        r.assert_called_once()
        assert summary["restarted"] is True

    def test_push_honours_backup_flag(self):
        src = config_source.K8sSource(k8s_backend.K8sTarget())
        with patch.object(k8s_backend, "write_config") as w:
            src.push({"nodes": []}, backup=False)
        assert w.call_args.kwargs["backup"] is False

    def test_describe_includes_namespace(self):
        src = config_source.K8sSource(k8s_backend.K8sTarget(namespace="ns1"))
        assert "ns1" in src.describe()
        assert src.describe().startswith("k8s://")


class TestSourceParity:
    """Both sources must satisfy the same contract the CLI relies on."""

    def test_both_expose_fetch_and_push(self):
        for src in (
            config_source.FileSource(Path("/tmp/x.yaml")),
            config_source.K8sSource(k8s_backend.K8sTarget()),
        ):
            assert callable(src.fetch)
            assert callable(src.push)
            assert isinstance(src.describe(), str)
            assert src.kind in ("file", "k8s")

    def test_push_summaries_share_common_keys(self, cfg_file):
        file_summary = config_source.FileSource(cfg_file).push({"nodes": []}, backup=False)
        with patch.object(k8s_backend, "write_config"):
            k8s_summary = config_source.K8sSource(k8s_backend.K8sTarget()).push({"nodes": []})
        common = {"source", "bytes_written", "backed_up", "restarted"}
        assert common <= set(file_summary)
        assert common <= set(k8s_summary)


class TestLegacyDelegation:
    """`config_yaml.fetch_yaml/push_yaml` must stay behaviourally identical to
    `K8sSource`, since they are now the same implementation. If someone
    reintroduces a second copy, these fail."""

    def test_fetch_yaml_matches_k8s_source(self):
        from cli_anything.espresense.core import config_yaml

        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "read_config", return_value=SAMPLE):
            legacy_raw, legacy_parsed = config_yaml.fetch_yaml(target)
            direct_raw, direct_parsed = config_source.K8sSource(target).fetch()
        assert legacy_raw == direct_raw == SAMPLE
        assert legacy_parsed["nodes"][0]["name"] == direct_parsed["nodes"][0]["name"]

    def test_push_yaml_matches_k8s_source(self):
        from cli_anything.espresense.core import config_yaml

        target = k8s_backend.K8sTarget()
        with patch.object(k8s_backend, "write_config"):
            legacy = config_yaml.push_yaml(target, {"nodes": []}, backup=True)
            direct = config_source.K8sSource(target).push({"nodes": []}, backup=True)
        assert legacy == direct

    def test_push_yaml_still_restarts(self):
        from cli_anything.espresense.core import config_yaml

        target = k8s_backend.K8sTarget()
        with (
            patch.object(k8s_backend, "write_config"),
            patch.object(k8s_backend, "restart") as r,
        ):
            summary = config_yaml.push_yaml(target, {"nodes": []}, restart=True)
        r.assert_called_once_with(target)
        assert summary["restarted"] is True
