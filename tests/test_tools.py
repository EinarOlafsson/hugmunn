"""Tool executor tests — path confinement is the part that must not regress."""

from __future__ import annotations

import pytest

from localagent.core import tools


@pytest.fixture()
def workdir(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    return str(tmp_path)


class TestPathConfinement:
    def test_relative_path_resolves(self, workdir):
        assert tools._safe_path(workdir, "notes.txt").name == "notes.txt"

    def test_parent_traversal_rejected(self, workdir):
        with pytest.raises(tools.ToolError, match="outside the working directory"):
            tools._safe_path(workdir, "../../../etc/passwd")

    def test_absolute_escape_rejected(self, workdir):
        with pytest.raises(tools.ToolError, match="outside the working directory"):
            tools._safe_path(workdir, "/etc/passwd")

    def test_symlink_escape_rejected(self, workdir, tmp_path_factory):
        import os

        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret").write_text("x", encoding="utf-8")
        os.symlink(outside / "secret", f"{workdir}/link")
        with pytest.raises(tools.ToolError):
            tools._safe_path(workdir, "link")

    def test_workdir_root_itself_allowed(self, workdir):
        assert tools._safe_path(workdir, ".") == tools._safe_path(workdir, "")


class TestReadFile:
    def test_reads_with_line_numbers(self, workdir):
        out = tools.execute("read_file", {"path": "notes.txt"}, workdir)
        assert "alpha" in out and "1\t" in out

    def test_respects_start_and_limit(self, workdir):
        out = tools.execute("read_file", {"path": "notes.txt", "start_line": 2, "max_lines": 1}, workdir)
        assert "beta" in out and "alpha" not in out

    def test_missing_file_is_recoverable(self, workdir):
        assert tools.execute("read_file", {"path": "nope.txt"}, workdir).startswith("Error:")


class TestWriteFile:
    def test_creates_file(self, workdir):
        out = tools.execute("write_file", {"path": "new.txt", "content": "hi"}, workdir)
        assert "Created" in out

    def test_creates_parent_dirs(self, workdir):
        tools.execute("write_file", {"path": "a/b/c.txt", "content": "x"}, workdir)
        assert tools._safe_path(workdir, "a/b/c.txt").read_text() == "x"

    def test_escape_rejected(self, workdir):
        out = tools.execute("write_file", {"path": "../evil.txt", "content": "x"}, workdir)
        assert out.startswith("Error:")


class TestSearchAndList:
    def test_list_directory(self, workdir):
        out = tools.execute("list_directory", {"path": "."}, workdir)
        assert "notes.txt" in out and "pkg/" in out

    def test_search_finds_match(self, workdir):
        out = tools.execute("search_text", {"pattern": "def hello", "glob": "*.py"}, workdir)
        assert "mod.py" in out

    def test_invalid_regex_is_recoverable(self, workdir):
        out = tools.execute("search_text", {"pattern": "([unclosed"}, workdir)
        assert out.startswith("Error:") or out == "no matches"


class TestRunCommand:
    def test_captures_stdout_and_exit_code(self, workdir):
        out = tools.execute("run_command", {"command": "echo hello"}, workdir)
        assert "hello" in out and "exit code: 0" in out

    def test_nonzero_exit_reported(self, workdir):
        out = tools.execute("run_command", {"command": "exit 3"}, workdir)
        assert "exit code: 3" in out


class TestRegistry:
    def test_unknown_tool_is_recoverable(self, workdir):
        assert tools.execute("nope", {}, workdir).startswith("Error: no such tool")

    def test_bad_arguments_recoverable(self, workdir):
        assert tools.execute("read_file", {"wrong": 1}, workdir).startswith("Error:")

    def test_mutating_tools_require_approval(self):
        assert tools.BY_NAME["write_file"].requires_approval
        assert tools.BY_NAME["run_command"].requires_approval
        assert not tools.BY_NAME["read_file"].requires_approval

    def test_schemas_are_openai_shaped(self):
        for schema in tools.schemas():
            assert schema["type"] == "function"
            assert {"name", "description", "parameters"} <= schema["function"].keys()

    def test_output_is_clipped(self):
        assert len(tools._clip("x" * (tools.MAX_OUTPUT_CHARS + 5000))) < tools.MAX_OUTPUT_CHARS + 200
