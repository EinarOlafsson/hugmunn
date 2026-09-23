"""Disk evaluation and the download flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from hugmunn import config
from hugmunn.core import downloads
from hugmunn.core.downloads import DiskReport, evaluate_disk


class TestDiskEvaluation:
    def test_reports_free_and_total(self, tmp_path):
        report = evaluate_disk(tmp_path)
        assert report.total_gb > 0
        assert 0 <= report.free_gb <= report.total_gb

    def test_walks_up_to_an_existing_parent(self, tmp_path):
        """A destination that does not exist yet must still be assessable."""
        report = evaluate_disk(tmp_path / "not" / "created" / "yet")
        assert report.total_gb > 0

    def test_rotational_flag_read_for_a_real_path(self):
        report = evaluate_disk(Path.home())
        assert report.kind in ("NVMe", "SSD", "HDD", "network", "unknown")


class TestVerdict:
    def test_refuses_when_short_on_space(self, tmp_path):
        ok, msg = DiskReport(tmp_path, free_gb=10, total_gb=100, kind="NVMe").verdict(87)
        assert not ok and "Not enough space" in msg and "77" in msg

    def test_accepts_with_room(self, tmp_path):
        ok, msg = DiskReport(tmp_path, free_gb=400, total_gb=1000, kind="NVMe").verdict(87)
        assert ok and "NVMe" in msg

    def test_warns_about_spinning_disks(self, tmp_path):
        ok, msg = DiskReport(tmp_path, free_gb=6000, total_gb=11000, kind="HDD").verdict(87)
        assert ok, "an HDD is usable, just slow"
        assert "spinning disk" in msg and "memory-mapped" in msg

    def test_warns_about_network_mounts(self, tmp_path):
        ok, msg = DiskReport(tmp_path, free_gb=1000, total_gb=44000, kind="network").verdict(87)
        assert ok and "network mount" in msg

    def test_warns_when_headroom_is_thin(self, tmp_path):
        ok, msg = DiskReport(tmp_path, free_gb=95, total_gb=1000, kind="NVMe").verdict(87)
        assert ok and "would remain" in msg

    def test_exact_fit_is_allowed(self, tmp_path):
        ok, _ = DiskReport(tmp_path, free_gb=87, total_gb=1000, kind="SSD").verdict(87)
        assert ok

    def test_is_fast_only_for_solid_state(self, tmp_path):
        assert DiskReport(tmp_path, 1, 1, kind="NVMe").is_fast
        assert DiskReport(tmp_path, 1, 1, kind="SSD").is_fast
        assert not DiskReport(tmp_path, 1, 1, kind="HDD").is_fast
        assert not DiskReport(tmp_path, 1, 1, kind="network").is_fast


class TestRegistryDownloadMetadata:
    def test_every_model_declares_a_source(self):
        missing = [m.key for m in config.REGISTRY if not m.repo or not m.files]
        assert not missing, f"no download source: {missing}"

    def test_sizes_are_plausible(self):
        for m in config.REGISTRY:
            assert 0 < m.download_gb < 200, f"{m.key}: {m.download_gb}"

    def test_sharded_models_list_every_shard(self):
        """A partial shard set loads with an opaque llama.cpp error."""
        import re
        for m in config.REGISTRY:
            for name in m.files:
                match = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", name)
                if match:
                    assert len(m.files) == int(match.group(2)), m.key

    def test_filenames_match_what_the_launch_script_loads(self):
        """The downloader must fetch the file the script will then open."""
        for m in config.REGISTRY:
            if not m.script_path.is_file():
                continue
            text = m.script_path.read_text()
            first = Path(m.files[0]).name
            assert first in text, f"{m.key}: script does not reference {first}"


class TestProgress:
    def test_percent(self):
        p = downloads.Progress(1, 3, "a.gguf", 5, 10, 50, 200)
        assert p.percent == 25.0

    def test_zero_total_does_not_divide_by_zero(self):
        assert downloads.Progress(1, 1, "a", 0, 0, 0, 0).percent == 0.0


@pytest.mark.usefixtures("model_scripts")
class TestDestinationMatchesTheLaunchScript:
    """The bug: repo paths were flattened, so 3 of 4 sharded models landed
    where llama.cpp never looks and reported themselves not-downloaded."""

    def test_expected_files_use_the_script_directory(self):
        spec = config.by_key("write-big")
        targets = spec.expected_files()
        assert len(targets) == 3
        for path in targets.values():
            assert path.parent == spec.model_path.parent

    def test_shards_are_siblings(self):
        """llama.cpp finds shard 2 and 3 beside shard 1."""
        for key in ("write-big", "code-q6", "agentic"):
            parents = {p.parent for p in config.by_key(key).expected_files().values()}
            assert len(parents) == 1, key

    def test_first_file_maps_to_the_model_argument(self):
        for m in config.REGISTRY:
            targets = m.expected_files()
            if not targets:
                continue
            assert targets[m.files[0]] == m.model_path, m.key

    def test_subdirectory_in_the_repo_path_is_not_carried_into_the_target(self):
        """UD-Q5_K_XL/ is a repo folder; the script uses its own layout."""
        spec = config.by_key("write-big")
        assert "UD-Q5_K_XL" in spec.files[0]
        target = spec.expected_files()[spec.files[0]]
        assert target.name == Path(spec.files[0]).name
        assert "qwen3.5-122b" in str(target)

    def test_missing_script_yields_no_targets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HUGMUNN_MODELS_ROOT", str(tmp_path))
        spec = config.ModelSpec("k", "L", "nope.sh", 1, "b",
                                repo="r", files=("a.gguf",), download_gb=1)
        assert spec.expected_files() == {}


class TestLinkIntoPlace:
    def test_links_when_paths_differ(self, tmp_path):
        actual = tmp_path / "elsewhere" / "m.gguf"
        actual.parent.mkdir()
        actual.write_bytes(b"weights")
        expected = tmp_path / "gguf" / "sub" / "m.gguf"
        downloads._link_into_place(actual, expected)
        assert expected.exists() and expected.read_bytes() == b"weights"

    def test_noop_when_paths_are_the_same(self, tmp_path):
        path = tmp_path / "m.gguf"
        path.write_bytes(b"x")
        downloads._link_into_place(path, path)
        assert path.read_bytes() == b"x"

    def test_replaces_a_stale_link(self, tmp_path):
        old = tmp_path / "old.gguf"; old.write_bytes(b"old")
        new = tmp_path / "new.gguf"; new.write_bytes(b"new")
        expected = tmp_path / "link.gguf"
        downloads._link_into_place(old, expected)
        downloads._link_into_place(new, expected)
        assert expected.read_bytes() == b"new"


@pytest.mark.usefixtures("model_scripts")
class TestUserChosenLocation:
    """Weights may live anywhere; the app remembers where."""

    def setup_method(self):
        config._MODEL_PATHS.clear()

    def teardown_method(self):
        config._MODEL_PATHS.clear()

    def test_override_changes_the_effective_path(self, tmp_path):
        spec = config.by_key("write-big")
        assert spec.model_path == spec.script_model_path
        config.set_model_path("write-big", tmp_path / "m.gguf")
        assert spec.model_path == tmp_path / "m.gguf"

    def test_override_can_be_cleared(self, tmp_path):
        config.set_model_path("write", tmp_path / "m.gguf")
        config.set_model_path("write", None)
        spec = config.by_key("write")
        assert spec.model_path == spec.script_model_path

    def test_availability_follows_the_override(self, tmp_path):
        spec = config.by_key("uncensored-big")
        weights = tmp_path / Path(spec.files[0]).name
        weights.write_bytes(b"x")
        config.set_model_path("uncensored-big", weights)
        assert spec.is_available()

    def test_shards_are_looked_for_beside_the_override(self, tmp_path):
        spec = config.by_key("write-big")
        first = tmp_path / Path(spec.files[0]).name
        first.write_bytes(b"x")
        config.set_model_path("write-big", first)
        assert not spec.is_available(), "one shard of three is not enough"
        for name in spec.files[1:]:
            (tmp_path / Path(name).name).write_bytes(b"x")
        assert spec.is_available()

    def test_settings_roundtrip_restores_the_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
        config.set_model_path("agentic", "/mnt/big/minimax/shard1.gguf")
        config.Settings().save()
        config._MODEL_PATHS.clear()
        assert config.by_key("agentic").model_path == config.by_key("agentic").script_model_path
        config.Settings.load()
        assert str(config.model_path_override("agentic")) == "/mnt/big/minimax/shard1.gguf"

    def test_launch_passes_the_override_as_an_argument(self, tmp_path, monkeypatch):
        """The scripts forward "$@" and llama.cpp takes the last --model."""
        import subprocess
        from hugmunn.core.server import ServerManager

        seen = {}

        class FakePopen:
            def __init__(self, cmd, **kw):
                seen["cmd"] = cmd
                self.stdout = None
            def poll(self): return 0
            def wait(self, timeout=None): return 0

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        config.set_model_path("write", tmp_path / "custom.gguf")
        mgr = ServerManager()
        try:
            mgr.start(config.by_key("write"), timeout=0.01)
        except Exception:
            pass
        assert "--model" in seen.get("cmd", [])
        assert str(tmp_path / "custom.gguf") in seen["cmd"]


class TestMissingShards:
    def test_reports_every_absent_sibling(self, tmp_path):
        spec = config.by_key("write-big")
        assert len(spec.missing_shards(tmp_path / "a.gguf")) == 3

    def test_empty_when_all_present(self, tmp_path):
        spec = config.by_key("code-q6")
        for name in spec.files:
            (tmp_path / Path(name).name).write_bytes(b"x")
        assert spec.missing_shards(tmp_path / Path(spec.files[0]).name) == []
