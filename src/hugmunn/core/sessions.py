"""Conversations on disk, so a crash costs nothing.

The saving has to happen *during* the conversation, not on exit. A clean
shutdown is exactly the case that does not need recovering; the one that does
is the process disappearing, and a save-on-quit never runs then. So every turn
is written as it completes.

Two things follow from that:

* **Writes are atomic.** A crash during the write must not leave a truncated
  file, because the file that gets corrupted is the one holding the work worth
  recovering. Written to a temporary name and renamed, which is atomic on
  POSIX.
* **A clean exit is recorded.** Otherwise there is no way to tell "you quit"
  from "it died", and offering to restore a conversation the user deliberately
  finished is noise that trains them to dismiss the prompt.

Sessions are pruned by count rather than age. A researcher who runs one long
conversation a week should still find last month's, and the files are a few
kilobytes each.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import config_dir


def session_dir() -> Path:
    """Resolved on each call so the environment can change under it."""
    return config_dir() / "sessions"

#: How many conversations to keep. Small files; the limit exists so the
#: directory does not grow without bound, not to save space.
KEEP = 50

#: Below this, a conversation is not worth offering to restore -- a single
#: message with no reply is usually a mistyped line, not lost work.
MIN_MESSAGES = 2


@dataclass
class Session:
    """One conversation, and enough state to put the app back where it was."""

    id: str
    started: float
    updated: float
    messages: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "local"
    model_key: str = ""
    model_label: str = ""
    #: The settings this conversation ran under. Restoring the words without
    #: these gives something that reads the same and behaves differently,
    #: which is worse than not restoring it: the difference does not show up
    #: until an answer is wrong.
    settings: dict[str, Any] = field(default_factory=dict)
    #: The goal in force, if any.
    goal: str = ""
    # False until the window closes normally. A session still False on the
    # next launch is one the process did not survive.
    closed_cleanly: bool = False

    @property
    def path(self) -> Path:
        return session_dir() / f"{self.id}.json"

    @property
    def title(self) -> str:
        """The first thing the user actually said, trimmed to a line.

        Derived rather than stored: a title written at creation time is
        written before there is anything to title.
        """
        for message in self.messages:
            if message.get("role") == "user":
                text = str(message.get("content") or "").strip()
                # Skip the marker a summarised conversation carries.
                if text.startswith("[Earlier in this conversation"):
                    continue
                if text:
                    line = text.splitlines()[0]
                    return line[:70] + ("…" if len(line) > 70 else "")
        return "(no messages)"

    @property
    def turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    @property
    def worth_restoring(self) -> bool:
        return len(self.messages) >= MIN_MESSAGES

    def age_phrase(self, now: float | None = None) -> str:
        seconds = max(0.0, (now if now is not None else time.time()) - self.updated)
        if seconds < 90:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)} minutes ago"
        if seconds < 86400:
            hours = int(seconds // 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = int(seconds // 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"

    def summary(self) -> str:
        return f"{self.title}  ·  {self.turns} turn(s), {self.age_phrase()}"


#: Incremented per id. The timestamp has second resolution and the pid is
#: constant within a process, so those two alone collide for a user who
#: presses Ctrl+L and types again inside a second -- and a collision here
#: means the new conversation overwrites the previous one's file.
_counter = 0


def new_id(now: float | None = None) -> str:
    """A sortable, human-readable, unique id.

    Three parts, each covering a case the others do not: the timestamp sorts
    and reads, the pid separates concurrent instances, and the counter
    separates conversations started within the same second in the same
    process.
    """
    global _counter
    _counter += 1
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return f"{stamp}-{os.getpid()}-{_counter}"


def save(session: Session) -> None:
    """Write atomically. A crash mid-write must not truncate the file."""
    session.updated = time.time()
    try:
        session_dir().mkdir(parents=True, exist_ok=True)
        tmp = session.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(session), indent=1), encoding="utf-8")
        tmp.replace(session.path)
    except OSError:
        # Losing the transcript is bad; taking the app down with it is worse.
        pass


def load(path: Path) -> Session | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    known = {f for f in Session.__dataclass_fields__}
    try:
        return Session(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None


def recent(limit: int = KEEP) -> list[Session]:
    """Saved conversations, newest first. Unreadable files are skipped."""
    if not session_dir().is_dir():
        return []
    found = []
    for path in session_dir().glob("*.json"):
        session = load(path)
        if session is not None and session.messages:
            found.append(session)
    found.sort(key=lambda s: s.updated, reverse=True)
    return found[:limit]


def unfinished() -> Session | None:
    """The most recent conversation the process did not close cleanly.

    ``None`` when the last session ended normally, which is the common case
    and must not produce a prompt -- offering to restore something the user
    deliberately finished teaches them to dismiss the dialog without reading
    it, which is precisely when it will matter.
    """
    for session in recent(limit=5):
        if not session.closed_cleanly and session.worth_restoring:
            return session
    return None


def mark_closed(session: Session) -> None:
    session.closed_cleanly = True
    save(session)


def prune(keep: int = KEEP) -> int:
    """Delete all but the newest ``keep``. Returns how many went."""
    if not session_dir().is_dir():
        return 0
    paths = sorted(
        (p for p in session_dir().glob("*.json")),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    removed = 0
    for path in paths[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    # Temporary files from an interrupted write are not conversations.
    for stale in session_dir().glob("*.json.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass
    return removed


def delete(session: Session) -> None:
    try:
        session.path.unlink()
    except OSError:
        pass
