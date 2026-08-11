"""Development tools: surgical edits, file discovery, diffs, code and SQL execution.

``edit_file`` is the important one here. Without it the only way to change a
file is ``write_file``, which means reproducing the whole thing — impossible for
anything long in a 16-64K context, and every regeneration is a chance to drop a
function. A string replacement that fails loudly when the target is ambiguous is
both cheaper and safer.
"""

from __future__ import annotations

import difflib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from .tools import MAX_OUTPUT_CHARS, ToolError, _clip, _safe_path


def edit_file(workdir: str, path: str, old: str, new: str, count: int = 1) -> str:
    """Replace an exact substring. Fails when the target is missing or ambiguous.

    Ambiguity is an error rather than a silent first-match replacement: if the
    model meant a different occurrence, a quiet substitution corrupts the file
    in a way nobody notices until much later.
    """
    target = _safe_path(workdir, path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    if not old:
        raise ToolError("`old` must not be empty — use write_file to create a file")

    try:
        original = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(f"could not read {path}: {exc}") from exc

    hits = original.count(old)
    if hits == 0:
        raise ToolError(
            f"`old` not found in {path}. It must match exactly, including "
            "indentation and line breaks. Read the file first."
        )
    if hits > int(count):
        raise ToolError(
            f"`old` appears {hits} times in {path} but count={count}. Include "
            "more surrounding context to make it unique, or raise count to "
            f"{hits} to replace them all."
        )

    updated = original.replace(old, new)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"could not write {path}: {exc}") from exc

    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2,
        )
    )
    return _clip(f"Replaced {hits} occurrence(s) in {target}\n\n{diff}")


def glob_files(workdir: str, pattern: str = "**/*.py", max_results: int = 200) -> str:
    """Find files by glob pattern, newest first."""
    root = _safe_path(workdir, ".")
    try:
        matches = [p for p in root.glob(pattern) if p.is_file()]
    except (ValueError, OSError) as exc:
        raise ToolError(f"bad pattern {pattern!r}: {exc}") from exc
    if not matches:
        return f"No files matching {pattern!r} under {root}"
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [f"  {p.relative_to(root)}  ({p.stat().st_size:,} B)" for p in matches[: int(max_results)]]
    extra = "" if len(matches) <= max_results else f"\n… {len(matches) - max_results} more"
    return _clip(f"{len(matches)} file(s) matching {pattern!r}:\n" + "\n".join(rows) + extra)


def diff_files(workdir: str, path_a: str, path_b: str) -> str:
    """Unified diff between two files."""
    a, b = _safe_path(workdir, path_a), _safe_path(workdir, path_b)
    for p, label in ((a, path_a), (b, path_b)):
        if not p.is_file():
            raise ToolError(f"not a file: {label}")
    try:
        left = a.read_text(encoding="utf-8", errors="replace").splitlines()
        right = b.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ToolError(f"could not read: {exc}") from exc
    diff = list(difflib.unified_diff(left, right, fromfile=path_a, tofile=path_b, lineterm=""))
    return _clip("\n".join(diff)) if diff else f"{path_a} and {path_b} are identical"


def python_exec(workdir: str, code: str, timeout: int = 60) -> str:
    """Run a Python snippet in a subprocess and return its output.

    A subprocess rather than ``exec`` in-process: a runaway loop or a crash
    takes down the child, not the application, and the timeout is enforceable.
    """
    root = Path(workdir).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"working directory does not exist: {root}")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir=root) as handle:
        handle.write(code)
        script = Path(handle.name)
    try:
        proc = subprocess.run(
            [sys.executable, str(script)], cwd=str(root),
            capture_output=True, text=True, timeout=int(timeout),
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"code timed out after {timeout}s") from None
    finally:
        script.unlink(missing_ok=True)

    parts = [f"exit code: {proc.returncode}"]
    if proc.stdout.strip():
        parts.append(f"--- stdout ---\n{proc.stdout.rstrip()}")
    if proc.stderr.strip():
        parts.append(f"--- stderr ---\n{proc.stderr.rstrip()}")
    if not proc.stdout.strip() and not proc.stderr.strip():
        parts.append("(no output — did you forget to print?)")
    return _clip("\n".join(parts))


def sql_query(workdir: str, database: str, query: str, max_rows: int = 100) -> str:
    """Run a read-only SQL query against a SQLite database.

    Opened read-only via a URI so a malformed or malicious query cannot mutate
    the file — spacr measurement databases are experimental records, and a
    stray UPDATE is unrecoverable.
    """
    target = _safe_path(workdir, database)
    if not target.is_file():
        raise ToolError(f"no such database: {database}")
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query).fetchmany(int(max_rows) + 1)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ToolError(f"SQL error: {exc}") from exc

    if not rows:
        return "Query returned no rows."
    truncated = len(rows) > max_rows
    rows = rows[: int(max_rows)]
    headers = list(rows[0].keys())
    widths = [max(len(h), *(len(str(r[h])) for r in rows)) for h in headers]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)),
           "  ".join("-" * w for w in widths)]
    out += ["  ".join(str(r[h]).ljust(w) for h, w in zip(headers, widths)) for r in rows]
    if truncated:
        out.append(f"… truncated at {max_rows} rows")
    return _clip("\n".join(out))


def sql_schema(workdir: str, database: str) -> str:
    """List tables, their columns, and row counts. Call before writing a query."""
    target = _safe_path(workdir, database)
    if not target.is_file():
        raise ToolError(f"no such database: {database}")
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            blocks = []
            for table in tables:
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                try:
                    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                except sqlite3.Error:
                    n = "?"
                spec = ", ".join(f"{c[1]} {c[2]}" for c in cols)
                blocks.append(f"{table}  ({n} rows)\n    {spec}")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ToolError(f"SQL error: {exc}") from exc
    return _clip(f"{target}\n\n" + "\n\n".join(blocks) if blocks else "No tables.")
