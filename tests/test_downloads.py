"""Disk evaluation and the download flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from localagent import config
from localagent.core import downloads
from localagent.core.downloads import DiskReport, evaluate_disk


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
            assert 10 < m.download_gb < 200, f"{m.key}: {m.download_gb}"

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
