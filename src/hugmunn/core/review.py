"""What a write actually changes, shown before it happens.

The approval dialog used to show the *content to be written* — the whole new
file, in a scroll box. For a new file that is the right thing. For an edit it
is close to useless: three hundred lines of which four differ, and no
indication which four. Approving that is not approving; it is clicking Allow
because reading it would take longer than the edit did.

So an edit to an existing file is shown as a diff, and the summary line says
what it comes to before the dialog is even read: *3 added, 1 removed, in
config.py*. That is the sentence a person makes the decision on.

Nothing here writes anything. It reads the file that is about to be replaced
and produces text.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

#: Lines of unchanged context either side of a change.
CONTEXT = 3

#: Hunks past this many are summarised rather than shown. A diff nobody
#: scrolls to the end of is not review.
MAX_HUNKS = 40


@dataclass(frozen=True)
class Change:
    """What one write would do to one path."""

    path: Path
    exists: bool
    added: int = 0
    removed: int = 0
    diff: str = ""
    binary: bool = False
    old_bytes: int = 0
    new_bytes: int = 0

    @property
    def is_new(self) -> bool:
        return not self.exists

    @property
    def is_noop(self) -> bool:
        return self.exists and not self.added and not self.removed and not self.binary

    def headline(self) -> str:
        """One line, which is what the decision is actually made on."""
        name = self.path.name or str(self.path)
        if self.is_new:
            lines = self.new_bytes and self.diff.count("\n")
            return f"Create {name} ({self.new_bytes:,} bytes)"
        if self.binary:
            return (f"Replace {name} — binary, "
                    f"{self.old_bytes:,} → {self.new_bytes:,} bytes")
        if self.is_noop:
            return f"{name} — no change; the content is identical"
        parts = []
        if self.added:
            parts.append(f"{self.added} added")
        if self.removed:
            parts.append(f"{self.removed} removed")
        return f"Edit {name} — {', '.join(parts)}"


def _looks_binary(text: str) -> bool:
    return "\0" in text[:4096]


def preview(path: str | Path, new_content: str) -> Change:
    """What writing ``new_content`` to ``path`` would change.

    Reads the existing file if there is one. A file that cannot be read --
    permissions, or genuinely binary -- is reported as a replacement rather
    than diffed against nonsense.
    """
    target = Path(path).expanduser()
    new_content = new_content if isinstance(new_content, str) else str(new_content)

    if not target.is_file():
        return Change(target, exists=False, new_bytes=len(new_content.encode()),
                      diff=new_content)

    try:
        old = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        return Change(target, exists=True, binary=True, old_bytes=size,
                      new_bytes=len(new_content.encode()))

    if _looks_binary(old) or _looks_binary(new_content):
        return Change(target, exists=True, binary=True, old_bytes=len(old.encode()),
                      new_bytes=len(new_content.encode()))

    old_lines = old.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    rows = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
        n=CONTEXT,
    ))

    added = sum(1 for r in rows if r.startswith("+") and not r.startswith("+++"))
    removed = sum(1 for r in rows if r.startswith("-") and not r.startswith("---"))

    hunks = sum(1 for r in rows if r.startswith("@@"))
    if hunks > MAX_HUNKS:
        kept: list[str] = []
        seen = 0
        for row in rows:
            if row.startswith("@@"):
                seen += 1
                if seen > MAX_HUNKS:
                    break
            kept.append(row)
        kept.append(f"\n[... {hunks - MAX_HUNKS} more hunk(s) not shown ...]\n")
        rows = kept

    return Change(
        target, exists=True, added=added, removed=removed,
        diff="".join(rows), old_bytes=len(old.encode()),
        new_bytes=len(new_content.encode()),
    )


def render(change: Change, colour: bool = False) -> str:
    """The diff as text. ``colour`` wraps it in HTML for the dialog."""
    if change.binary:
        return ("This is a binary file. It would be replaced wholesale, and "
                "there is no meaningful diff to show.")
    if change.is_noop:
        return "The new content is byte-identical to what is already there."
    if not colour:
        return change.diff
    return html(change)


def html(change: Change, palette: dict | None = None) -> str:
    """The diff marked up for the approval dialog.

    Colours come from the palette so it reads on all eight themes; the
    fallbacks are only for a caller with no palette to hand.
    """
    import html as html_mod

    palette = palette or {}
    add = palette.get("success", "#5ec27a")
    remove = palette.get("error", "#e0685f")
    meta = palette.get("fg_dim", "#8b93a1")
    body = palette.get("fg", "#dde1e7")

    if change.is_new:
        return (f"<pre style='color:{body}'>"
                f"{html_mod.escape(change.diff[:20000])}</pre>")

    rows = []
    for line in change.diff.splitlines():
        text = html_mod.escape(line)
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            rows.append(f"<span style='color:{meta}'>{text}</span>")
        elif line.startswith("+"):
            rows.append(f"<span style='color:{add}'>{text}</span>")
        elif line.startswith("-"):
            rows.append(f"<span style='color:{remove}'>{text}</span>")
        else:
            rows.append(f"<span style='color:{body}'>{text}</span>")
    return "<pre style='margin:0'>" + "\n".join(rows) + "</pre>"
