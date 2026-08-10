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
)

BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS]


def execute(name: str, arguments: dict[str, Any], workdir: str) -> str:
    tool = BY_NAME.get(name)
    if tool is None:
        return f"Error: no such tool {name!r}. Available: {', '.join(BY_NAME)}"
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
