"""Autonomy policy.

The property that must not regress: system paths and installed packages stay
protected at *every* level, including the top one, while an editable install's
source stays writable — that distinction is what makes level 4 usable rather
than merely dangerous.
"""

from __future__ import annotations

import pytest

from localagent.core.autonomy import Autonomy, decide

SITE = "/home/u/anaconda3/envs/spacr/lib/python3.10/site-packages/numpy/core.py"
EDITABLE = "/mnt/firecuda2/Claude/repo/spacr/spacr/utils.py"


class TestLevelOne:
    def test_confirms_even_reads(self, tmp_path):
        assert decide("read_file", {"path": "a.py"}, str(tmp_path), Autonomy.CONFIRM_ALL).needs_approval

    def test_confirms_writes(self, tmp_path):
        assert decide("write_file", {"path": "a.py"}, str(tmp_path), Autonomy.CONFIRM_ALL).needs_approval


class TestLevelTwo:
    def test_reads_run_freely(self, tmp_path):
        assert not decide("read_file", {"path": "a.py"}, str(tmp_path), Autonomy.ASK_TO_WRITE).needs_approval

    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_command", "python_exec"])
    def test_anything_mutating_asks(self, tmp_path, tool):
        assert decide(tool, {"path": "a.py"}, str(tmp_path), Autonomy.ASK_TO_WRITE).needs_approval


class TestLevelThree:
    def test_write_inside_workspace_is_free(self, tmp_path):
        assert not decide("write_file", {"path": "a.py"}, str(tmp_path), Autonomy.WORKSPACE).needs_approval

    def test_write_outside_workspace_asks(self, tmp_path):
        d = decide("write_file", {"path": "/home/u/notes.txt"}, str(tmp_path), Autonomy.WORKSPACE)
        assert d.needs_approval and "outside" in d.reason

    def test_traversal_out_of_workspace_asks(self, tmp_path):
        assert decide("write_file", {"path": "../escape.txt"}, str(tmp_path), Autonomy.WORKSPACE).needs_approval

    def test_shell_asks_because_its_reach_is_unknowable(self, tmp_path):
        """A command string has no parseable path; it cannot be shown to stay in."""
        d = decide("run_command", {"command": "ls"}, str(tmp_path), Autonomy.WORKSPACE)
        assert d.needs_approval and "outside" in d.reason


class TestLevelFourStillProtects:
    def test_system_paths_refused(self, tmp_path):
        for target in ("/etc/passwd", "/usr/bin/python", "/boot/vmlinuz", "/var/log/x"):
            d = decide("write_file", {"path": target}, str(tmp_path), Autonomy.FULL)
            assert d.needs_approval, target
            assert "system" in d.reason

    def test_installed_packages_refused(self, tmp_path):
        d = decide("write_file", {"path": SITE}, str(tmp_path), Autonomy.FULL)
        assert d.needs_approval and "installed packages" in d.reason

    def test_editable_install_source_is_writable(self, tmp_path):
        """pip install -e keeps source out of site-packages, so it stays editable."""
        assert not decide("write_file", {"path": EDITABLE}, str(tmp_path), Autonomy.FULL).needs_approval

    def test_ordinary_paths_outside_workspace_are_free(self, tmp_path):
        assert not decide("write_file", {"path": "/home/u/notes.txt"}, str(tmp_path), Autonomy.FULL).needs_approval

    def test_shell_runs_freely(self, tmp_path):
        assert not decide("run_command", {"command": "pytest"}, str(tmp_path), Autonomy.FULL).needs_approval


class TestProtectionHoldsAtEveryLevel:
    @pytest.mark.parametrize("level", list(Autonomy))
    @pytest.mark.parametrize("target", ["/etc/hosts", SITE])
    def test_never_free(self, tmp_path, level, target):
        assert decide("write_file", {"path": target}, str(tmp_path), level).needs_approval


class TestMonotonicity:
    """A higher level must never ask for something a lower level allowed."""

    @pytest.mark.parametrize("tool,args", [
        ("read_file", {"path": "a.py"}),
        ("write_file", {"path": "a.py"}),
        ("write_file", {"path": "/home/u/x.txt"}),
        ("run_command", {"command": "ls"}),
        ("write_file", {"path": SITE}),
    ])
    def test_permissiveness_is_monotonic(self, tmp_path, tool, args):
        asks = [decide(tool, args, str(tmp_path), lvl).needs_approval for lvl in Autonomy]
        # once it stops asking it must not start again
        assert asks == sorted(asks, reverse=True), asks
