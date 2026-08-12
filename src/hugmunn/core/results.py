"""Keeps big tool output out of the context without losing it.

A single ``read_file`` on a real source file is four to eight thousand tokens.
Three of those and a 16K window is gone before the model has done anything with
what it read — and the model usually needed one function out of the file.

So the full output is kept here and the conversation gets a digest: the shape
of the thing, the first and last of it, and a handle. If the model needs more
it calls ``recall`` and gets the part it asks for.

This is not compression. Nothing is thrown away — the store holds the exact
bytes for the life of the session, and the digest is generated rather than
guessed at. What changes is only what occupies the window.

The threshold matters more than the mechanism. Digesting a 200-token result
costs a round trip to recover something that would have fitted anyway, so
small results pass through untouched and the model never learns that tool
output is unreliable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Results under this many characters go through whole. Roughly 500 tokens --
#: comfortably affordable, and below the size where a model would need to ask
#: for more anyway.
INLINE_LIMIT = 2000

#: How much of a digested result to show, split between the head and the tail.
#: The head says what the thing is; the tail is where errors and totals live.
HEAD_CHARS = 900
TAIL_CHARS = 400


@dataclass
class Stored:
    """One tool result, whole."""

    handle: str
    tool: str
    summary: str
    content: str

    @property
    def lines(self) -> int:
        return self.content.count("\n") + 1

    @property
    def chars(self) -> int:
        return len(self.content)


@dataclass
class ResultStore:
    """Every tool result this session produced, addressable by handle.

    Per session rather than global: handles appear in the transcript, a
    restored conversation shows them, and a handle that resolves to a
    different file than it did yesterday is worse than one that has expired.
    """

    _items: dict[str, Stored] = field(default_factory=dict)
    _counter: int = 0

    def __len__(self) -> int:
        return len(self._items)

    def store(self, tool: str, summary: str, content: str) -> Stored:
        self._counter += 1
        handle = f"r{self._counter}"
        item = Stored(handle, tool, summary, content)
        self._items[handle] = item
        return item

    def get(self, handle: str) -> Stored | None:
        return self._items.get((handle or "").strip().lstrip("#"))

    def clear(self) -> None:
        self._items.clear()
        self._counter = 0

    # ------------------------------------------------------------- digesting

    def digest(self, tool: str, summary: str, content: str) -> str:
        """What goes into the conversation in place of a large result.

        Small results are returned untouched: a model that learns tool output
        is sometimes truncated starts hedging about everything it read, which
        costs more than the tokens saved.
        """
        if len(content) <= INLINE_LIMIT:
            return content

        item = self.store(tool, summary, content)
        head = content[:HEAD_CHARS]
        tail = content[-TAIL_CHARS:]
        hidden = item.chars - len(head) - len(tail)
        return (
            f"{head}\n"
            f"\n[... {hidden:,} characters not shown. Full result is {item.lines:,} "
            f"lines, {item.chars:,} characters, handle {item.handle} ...]\n\n"
            f"{tail}\n"
            f"\n[Use recall(\"{item.handle}\") for the whole thing, or "
            f"recall(\"{item.handle}\", find=\"...\") to search it.]"
        )


# ------------------------------------------------------------- the recall tool

SCHEMA = {
    "type": "object",
    "properties": {
        "handle": {
            "type": "string",
            "description": "The handle from a truncated result, e.g. r3.",
        },
        "find": {
            "type": "string",
            "description": "Optional. Return only lines matching this, with "
                           "a little context around each. Use it rather than "
                           "asking for the whole thing.",
        },
        "start": {
            "type": "integer",
            "description": "Optional. First line to return, 1-based.",
        },
        "count": {
            "type": "integer",
            "description": "Optional. How many lines from `start`. Default 200.",
        },
    },
    "required": ["handle"],
}

DESCRIPTION = (
    "Retrieve a tool result that was too large to include in full. Prefer "
    "`find` over fetching everything: the result was set aside because it did "
    "not fit, and pulling all of it back in defeats the point."
)

#: Lines of context either side of a `find` hit.
CONTEXT_LINES = 3

#: Ceiling on what one recall may return, so it cannot undo the digesting.
MAX_RETURN = 8000


def recall(store: ResultStore, handle: str, find: str = "",
           start: int = 0, count: int = 200) -> str:
    """Serve part of a stored result. Never more than :data:`MAX_RETURN`."""
    item = store.get(handle)
    if item is None:
        known = ", ".join(sorted(store._items)) or "none"
        return f"No result with handle {handle!r}. Available: {known}"

    lines = item.content.splitlines()

    if find:
        try:
            pattern = re.compile(find, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(find), re.IGNORECASE)
        hits = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not hits:
            return (f"No line of {handle} matches {find!r}. "
                    f"It is {len(lines):,} lines.")
        # Merge overlapping context windows so a dense match does not repeat
        # the same lines several times over.
        wanted: set[int] = set()
        for hit in hits:
            wanted.update(range(max(0, hit - CONTEXT_LINES),
                                min(len(lines), hit + CONTEXT_LINES + 1)))
        out, previous = [], None
        for index in sorted(wanted):
            if previous is not None and index != previous + 1:
                out.append("        ...")
            out.append(f"{index + 1:6}  {lines[index]}")
            previous = index
        body = "\n".join(out)
        header = (f"{len(hits)} matching line(s) in {handle} "
                  f"({len(lines):,} lines total)\n\n")
        return (header + body)[:MAX_RETURN]

    first = max(0, (start or 1) - 1)
    body = "\n".join(f"{i + 1:6}  {lines[i]}"
                     for i in range(first, min(len(lines), first + max(1, count))))
    shown_to = min(len(lines), first + max(1, count))
    footer = ""
    if shown_to < len(lines):
        footer = (f"\n\n[lines {shown_to + 1}-{len(lines)} not shown; "
                  f"recall(\"{handle}\", start={shown_to + 1})]")
    return (f"{handle}, lines {first + 1}-{shown_to} of {len(lines):,}\n\n"
            + body + footer)[:MAX_RETURN]


def make_recall_tool(store: ResultStore):
    """Build the tool, bound to one session's store."""
    from .tools import Tool

    def run(workdir: str, handle: str = "", find: str = "",
            start: int = 0, count: int = 200) -> str:
        return recall(store, handle, find, int(start or 0), int(count or 200))

    return Tool(name="recall", description=DESCRIPTION, parameters=SCHEMA, run=run)
