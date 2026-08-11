"""Tool definitions and their executors.

Every tool argument is untrusted model output. Two rules follow from that and
are enforced here rather than left to the caller:

* Filesystem paths are resolved to canonical form and must stay inside the
  session working directory — ``..``, symlinks, and absolute escapes are
  rejected before any file is opened.
* Mutating tools (``write_file``, ``run_command``) are marked
  ``requires_approval`` so the UI can gate them behind a human decision. A
  local model with unattended shell access is a bad trade for convenience.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import web

MAX_OUTPUT_CHARS = 20_000  # keep a single tool result from blowing the context


class ToolError(Exception):
    """Recoverable failure; the message is fed back to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., str]
    requires_approval: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------- path safety


def _safe_path(workdir: str, raw: str) -> Path:
    """Resolve ``raw`` against ``workdir`` and refuse anything that escapes it."""
    root = Path(workdir).expanduser().resolve()
    candidate = Path(raw).expanduser()
    target = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if target != root and root not in target.parents:
        raise ToolError(
            f"path {raw!r} resolves outside the working directory ({root}). "
            "Change the working directory in the sidebar if this is intended."
        )
    return target


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… [truncated, {len(text) - MAX_OUTPUT_CHARS} more chars]"


# -------------------------------------------------------------- implementations


def _read_file(workdir: str, path: str, start_line: int = 1, max_lines: int = 800) -> str:
    target = _safe_path(workdir, path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ToolError(f"could not read {path}: {exc}") from exc
    start = max(1, int(start_line))
    chunk = lines[start - 1 : start - 1 + max(1, int(max_lines))]
    numbered = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(chunk))
    header = f"{target} ({len(lines)} lines total)\n"
    return _clip(header + numbered)


def _write_file(workdir: str, path: str, content: str) -> str:
    target = _safe_path(workdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.is_file()
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"could not write {path}: {exc}") from exc
    verb = "Overwrote" if existed else "Created"
    return f"{verb} {target} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"


def _list_directory(workdir: str, path: str = ".", max_entries: int = 300) -> str:
    target = _safe_path(workdir, path)
    if not target.is_dir():
        raise ToolError(f"not a directory: {path}")
    rows: list[str] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        raise ToolError(f"could not list {path}: {exc}") from exc
    for entry in entries[: int(max_entries)]:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            rows.append(f"  {entry.name}/")
        else:
            try:
                rows.append(f"  {entry.name}  ({entry.stat().st_size:,} B)")
            except OSError:
                rows.append(f"  {entry.name}")
    return _clip(f"{target}\n" + "\n".join(rows) if rows else f"{target}\n  (empty)")


def _search_text(workdir: str, pattern: str, path: str = ".", glob: str = "*") -> str:
    """Literal/regex search across files, preferring ripgrep when installed."""
    import re
    import shutil

    target = _safe_path(workdir, path)
    rg = shutil.which("rg")
    if rg:
        try:
            proc = subprocess.run(
                [rg, "--line-number", "--no-heading", "--color=never",
                 "--max-count=40", "--glob", glob, pattern, str(target)],
                capture_output=True, text=True, timeout=45,
            )
            return _clip(proc.stdout or "no matches")
        except (subprocess.SubprocessError, OSError):
            pass  # fall through to the pure-Python path

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex {pattern!r}: {exc}") from exc

    hits: list[str] = []
    for file in target.rglob(glob):
        if not file.is_file() or len(hits) >= 200:
            continue
        try:
            for n, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{file}:{n}:{line.strip()[:200]}")
                    if len(hits) >= 200:
                        break
        except OSError:
            continue
    return _clip("\n".join(hits) or "no matches")


def _run_command(workdir: str, command: str, timeout: int = 120) -> str:
    root = Path(workdir).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"working directory does not exist: {root}")
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            text=True, timeout=int(timeout),
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"command timed out after {timeout}s") from None
    except OSError as exc:
        raise ToolError(f"could not run command: {exc}") from exc
    parts = [f"$ {command}", f"exit code: {proc.returncode}"]
    if proc.stdout.strip():
        parts.append(f"--- stdout ---\n{proc.stdout.rstrip()}")
    if proc.stderr.strip():
        parts.append(f"--- stderr ---\n{proc.stderr.rstrip()}")
    return _clip("\n".join(parts))


# ------------------------------------------------------------------- registry

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="read_file",
        description=(
            "Read a UTF-8 text file from the working directory. Returns "
            "line-numbered content. Call this before editing a file so you edit "
            "what is actually there."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the working directory."},
                "start_line": {"type": "integer", "description": "1-based first line. Default 1."},
                "max_lines": {"type": "integer", "description": "How many lines to return. Default 800."},
            },
            "required": ["path"],
        },
        run=_read_file,
    ),
    Tool(
        name="list_directory",
        description="List files and subdirectories. Use it to orient before reading or searching.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the working directory. Default '.'"},
            },
            "required": [],
        },
        run=_list_directory,
    ),
    Tool(
        name="search_text",
        description=(
            "Search file contents by regular expression. Use this to locate a "
            "symbol or string when you do not know which file holds it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path": {"type": "string", "description": "Directory to search. Default '.'"},
                "glob": {"type": "string", "description": "Filename glob filter, e.g. '*.py'. Default '*'."},
            },
            "required": ["pattern"],
        },
        run=_search_text,
    ),
    Tool(
        name="write_file",
        description=(
            "Write a file, creating or overwriting it. Provide the complete "
            "final content — this does not patch. Requires user approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the working directory."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            "required": ["path", "content"],
        },
        run=_write_file,
        requires_approval=True,
    ),
    Tool(
        name="run_command",
        description=(
            "Run a shell command in the working directory and return its output. "
            "Use for tests, builds, and git. Requires user approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {"type": "integer", "description": "Seconds before the command is killed. Default 120."},
            },
            "required": ["command"],
        },
        run=_run_command,
        requires_approval=True,
    ),
    Tool(
        name="web_search",
        description=(
            "Search the public web and return titles, URLs, and snippets. Call "
            "this when the answer depends on current information, on something "
            "after your training cutoff, or on a fact you are not confident "
            "about. Follow up with web_fetch to read a promising result."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "How many results to return. Default 8."},
            },
            "required": ["query"],
        },
        run=web.web_search,
    ),
    Tool(
        name="web_fetch",
        description=(
            "Fetch a public http/https URL and return its readable text with "
            "markup stripped. Use it to read a page found via web_search, or a "
            "URL the user provided. Public internet only — it cannot reach this "
            "machine or the local network."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
                "max_chars": {"type": "integer", "description": "Truncate the page to this length. Default 15000."},
            },
            "required": ["url"],
        },
        run=web.web_fetch,
    ),
    Tool(
        name="image_search",
        description=(
            "Search the web for images. Returns image URLs, the page each came "
            "from, and pixel dimensions. Use when the user wants pictures, "
            "diagrams, or reference imagery rather than text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "max_results": {"type": "integer", "description": "How many to return. Default 12."},
            },
            "required": ["query"],
        },
        run=web.image_search,
    ),
    Tool(
        name="download_pdfs",
        description=(
            "Find every PDF linked from a web page and download them into the "
            "working directory. Use for collecting papers from a publication "
            "list, lab page, or search-result page. Requires user approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page to scan for PDF links."},
                "subdir": {"type": "string", "description": "Folder under the working directory. Default 'pdfs'."},
                "max_files": {"type": "integer", "description": "Cap on downloads. Default 10."},
            },
            "required": ["url"],
        },
        run=web.download_pdfs,
        requires_approval=True,
    ),
)


def _p(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


# Registered after TOOLS is defined so devtools/research can import helpers from
# this module without a circular import.
def _late_tools() -> tuple["Tool", ...]:
    from . import devtools, research

    S = lambda d: {"type": "string", "description": d}  # noqa: E731
    I = lambda d: {"type": "integer", "description": d}  # noqa: E731

    return (
        Tool(
            name="edit_file",
            description=(
                "Replace an exact substring in a file. Prefer this over "
                "write_file for any change to an existing file — it does not "
                "require reproducing the whole file, and it fails loudly if the "
                "target text is missing or ambiguous instead of corrupting "
                "silently. Read the file first so `old` matches exactly."
            ),
            parameters=_p({
                "path": S("File to edit, relative to the working directory."),
                "old": S("Exact text to replace, including indentation."),
                "new": S("Replacement text."),
                "count": I("Expected number of occurrences. Default 1."),
            }, ["path", "old", "new"]),
            run=devtools.edit_file,
            requires_approval=True,
        ),
        Tool(
            name="glob_files",
            description=(
                "Find files by glob pattern (e.g. '**/*.py', 'data/*.csv'), "
                "newest first. Use to orient in an unfamiliar tree before reading."
            ),
            parameters=_p({
                "pattern": S("Glob pattern. Default '**/*.py'."),
                "max_results": I("Cap on results. Default 200."),
            }, []),
            run=devtools.glob_files,
        ),
        Tool(
            name="diff_files",
            description="Unified diff between two files. Use to compare versions or verify an edit.",
            parameters=_p({"path_a": S("First file."), "path_b": S("Second file.")},
                          ["path_a", "path_b"]),
            run=devtools.diff_files,
        ),
        Tool(
            name="python_exec",
            description=(
                "Run a Python snippet in the working directory and return its "
                "output. Use for calculations, data inspection, and quick checks "
                "you cannot do reliably in your head. Print what you want back. "
                "Requires user approval."
            ),
            parameters=_p({
                "code": S("Python source to execute."),
                "timeout": I("Seconds before it is killed. Default 60."),
            }, ["code"]),
            run=devtools.python_exec,
            requires_approval=True,
        ),
        Tool(
            name="sql_schema",
            description=(
                "List the tables, columns, and row counts of a SQLite database. "
                "Always call this before sql_query — guessing column names wastes "
                "a round trip."
            ),
            parameters=_p({"database": S("Path to the .db file.")}, ["database"]),
            run=devtools.sql_schema,
        ),
        Tool(
            name="sql_query",
            description=(
                "Run a read-only SQL query against a SQLite database and return "
                "rows as a table. The connection is opened read-only, so writes "
                "are rejected."
            ),
            parameters=_p({
                "database": S("Path to the .db file."),
                "query": S("SQL SELECT statement."),
                "max_rows": I("Cap on rows returned. Default 100."),
            }, ["database", "query"]),
            run=devtools.sql_query,
        ),
        Tool(
            name="pubmed_search",
            description=(
                "Search PubMed and return structured records — title, authors, "
                "journal, year, PMID, DOI, abstract. Use this rather than "
                "web_search for any literature question: the fields are real, so "
                "a citation built from them is real."
            ),
            parameters=_p({
                "query": S("Search terms, e.g. 'Toxoplasma bradyzoite differentiation'."),
                "max_results": I("How many records. Default 8."),
            }, ["query"]),
            run=research.pubmed_search,
        ),
        Tool(
            name="arxiv_search",
            description=(
                "Search arXiv and return structured records with abstracts and "
                "direct PDF links. Use for preprints, methods, and computational work."
            ),
            parameters=_p({
                "query": S("Search terms."),
                "max_results": I("How many records. Default 8."),
            }, ["query"]),
            run=research.arxiv_search,
        ),
        Tool(
            name="read_pdf",
            description=(
                "Extract the text of a local PDF. Pairs with download_pdfs: "
                "collect papers, then actually read them."
            ),
            parameters=_p({
                "path": S("PDF file, relative to the working directory."),
                "max_chars": I("Truncate to this length. Default 12000."),
            }, ["path"]),
            run=research.read_pdf,
        ),
        Tool(
            name="image_info",
            description=(
                "Report dimensions, bit depth, channel mode, frame count, and "
                "metadata of a local image. Use on microscopy files to check "
                "acquisition settings before analysing — multi-frame TIFFs are "
                "z-stacks or time series."
            ),
            parameters=_p({"path": S("Image file, relative to the working directory.")},
                          ["path"]),
            run=research.image_info,
        ),
    )


_CORE_TOOLS = TOOLS
del TOOLS  # rebuilt lazily below

_ALL: tuple[Tool, ...] | None = None


def _all_tools() -> tuple[Tool, ...]:
    """Build the full registry on first use.

    ``devtools`` and ``research`` import helpers from this module, so importing
    them at module scope makes the import order load-bearing: importing
    ``research`` first raised a partially-initialised-module error while
    importing ``tools`` first quietly worked. Deferring to first attribute
    access removes the ordering dependency entirely.
    """
    global _ALL
    if _ALL is None:
        _ALL = _CORE_TOOLS + _late_tools()
    return _ALL


def __getattr__(name: str):  # PEP 562
    if name == "TOOLS":
        return _all_tools()
    if name == "BY_NAME":
        return {t.name: t for t in _all_tools()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def by_name() -> dict[str, Tool]:
    """Name -> Tool for the full registry. ``tools.BY_NAME`` is the lazy alias."""
    return {t.name: t for t in _all_tools()}


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in _all_tools()]


def execute(
    name: str,
    arguments: dict[str, Any],
    workdir: str,
    extra: dict[str, Tool] | None = None,
) -> str:
    """Run a tool. ``extra`` holds user plugins; built-ins always win a name clash."""
    registry = by_name()
    tool = registry.get(name) or (extra or {}).get(name)
    if tool is None:
        known = ", ".join(sorted(set(registry) | set(extra or {})))
        return f"Error: no such tool {name!r}. Available: {known}"
    try:
        return tool.run(workdir, **arguments)
    except (ToolError, web.WebError) as exc:
        return f"Error: {exc}"
    except TypeError as exc:
        return f"Error: bad arguments for {name}: {exc}"
    except Exception as exc:  # a tool bug must not kill the agent loop
        return f"Error: {type(exc).__name__}: {exc}"


def summarize_call(name: str, arguments: dict[str, Any]) -> str:
    """One-line human description used in the approval prompt and transcript."""
    if name == "run_command":
        return arguments.get("command", "")
    if name == "write_file":
        content = arguments.get("content", "")
        return f"{arguments.get('path', '?')}  ({len(content)} chars)"
    if name == "search_text":
        return f"/{arguments.get('pattern', '')}/ in {arguments.get('path', '.')}"
    return arguments.get("path", "") or ", ".join(f"{k}={v!r}" for k, v in arguments.items())
