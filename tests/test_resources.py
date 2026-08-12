"""Resource sampling.

Sampling must never raise: it runs on a QTimer every second, so an exception
would fire repeatedly and take the UI with it. Every parser degrades to zeros
instead.
"""

from __future__ import annotations

from hugmunn.core import resources
from hugmunn.core.resources import Sampler, Snapshot


class TestSampling:
    def test_sample_returns_a_snapshot(self):
        assert isinstance(Sampler().sample(), Snapshot)

    def test_cpu_percent_is_bounded(self):
        s = Sampler()
        s.sample()
        assert 0.0 <= s.sample().cpu_percent <= 100.0

    def test_memory_is_positive_on_a_real_machine(self):
        snap = Sampler().sample()
        assert snap.ram_total_gb > 0
        assert 0 <= snap.ram_used_gb <= snap.ram_total_gb

    def test_repeated_sampling_is_stable(self):
        s = Sampler()
        for _ in range(5):
            assert 0.0 <= s.sample().cpu_percent <= 100.0


class TestDegradesRatherThanRaising:
    def test_missing_proc_stat_gives_zero_cpu(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resources, "_STAT", tmp_path / "absent")
        assert Sampler().sample().cpu_percent == 0.0

    def test_missing_meminfo_gives_zero_memory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resources, "_MEMINFO", tmp_path / "absent")
        snap = Sampler().sample()
        assert snap.ram_total_gb == 0.0

    def test_garbage_meminfo_does_not_raise(self, monkeypatch, tmp_path):
        bad = tmp_path / "meminfo"
        bad.write_text("MemTotal: not-a-number\nnonsense\n", encoding="utf-8")
        monkeypatch.setattr(resources, "_MEMINFO", bad)
        assert Sampler().sample().ram_total_gb == 0.0

    def test_no_nvidia_smi_means_no_gpu_fields(self, monkeypatch):
        monkeypatch.setattr(resources.shutil, "which", lambda _: None)
        snap = Sampler().sample()
        assert not snap.has_gpu
        assert snap.gpu_percent is None

    def test_nvidia_smi_failure_is_swallowed(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("nvidia-smi exploded")
        monkeypatch.setattr(resources.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(resources.subprocess, "run", boom)
        assert not Sampler().sample().has_gpu


class TestDerivedFields:
    def test_percentages(self):
        snap = Snapshot(ram_used_gb=50, ram_total_gb=100, vram_used_gb=12, vram_total_gb=24)
        assert snap.ram_percent == 50.0
        assert snap.vram_percent == 50.0

    def test_zero_totals_do_not_divide_by_zero(self):
        snap = Snapshot()
        assert snap.ram_percent == 0.0 and snap.vram_percent == 0.0


class TestWarnings:
    """The note should name the specific problem, not say 'high usage'."""

    def _warn(self, **kw):
        from hugmunn.ui.resource_bar import ResourceBar
        return ResourceBar._warning(Snapshot(**kw))

    def test_swap_in_use_is_reported_first(self):
        note = self._warn(ram_used_gb=10, ram_total_gb=100, swap_used_gb=4.0)
        assert "Swap" in note and "tok/s" in note

    def test_exhausted_vram_is_reported(self):
        note = self._warn(ram_used_gb=10, ram_total_gb=100,
                          vram_used_gb=23.5, vram_total_gb=24.0, gpu_procs=3)
        assert "VRAM" in note and "3 process" in note

    def test_low_ram_is_reported(self):
        note = self._warn(ram_used_gb=96, ram_total_gb=100)
        assert "RAM free" in note

    def test_healthy_machine_says_nothing(self):
        assert self._warn(ram_used_gb=20, ram_total_gb=100,
                          vram_used_gb=1.0, vram_total_gb=24.0) == ""
