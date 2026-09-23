"""Autonomy levels: what a model may change without asking first.

Four tiers, from confirm-everything to broad freedom. The policy lives here
rather than in the UI so that the decision is testable and so that a bug in a
widget cannot silently widen it.

The top tier is *not* unrestricted. Two classes of path stay protected at every
level, because a model editing them breaks the machine rather than the project:

* **System directories** — ``/usr``, ``/etc``, ``/bin`` and friends.
* **Installed packages** — anything under a ``site-packages`` or
  ``dist-packages`` directory.

The second exclusion deliberately does *not* cover editable installs. A
``pip install -e`` package keeps its source in the working repository and only
leaves a finder stub in ``site-packages``, so protecting ``site-packages``
protects the installed copy while leaving development sources fully writable —
which is what makes the top tier useful for a working environment rather than
merely dangerous.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

# Directories that are never freely writable, whatever the level.
SYSTEM_ROOTS = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/etc",
    "/sys", "/proc", "/dev", "/run", "/var", "/opt",
)

# Tool names that only read. Everything else is treated as mutating.
READ_ONLY = frozenset({
    "read_file", "list_directory", "search_text", "glob_files", "diff_files",
    "web_search", "web_fetch", "image_search", "sql_schema", "sql_query",
    "pubmed_search", "arxiv_search", "read_pdf", "image_info",
})

# Arguments that name a filesystem destination, in priority order.
PATH_ARGS = ("path", "path_a", "database", "subdir", "dest")


class Autonomy(IntEnum):
    """Approval policy, from confirming every call (1) to broad tool access (4).

    These checks inspect tool arguments; they do not sandbox processes.
    """

    CONFIRM_ALL = 1     # every tool call is confirmed, reads included
    ASK_TO_WRITE = 2    # reads run freely; anything that changes state asks
    WORKSPACE = 3       # free inside the working directory; asks outside it
    FULL = 4            # free anywhere except system paths and installed packages


LABELS = {
    Autonomy.CONFIRM_ALL: "1 · Confirm everything",
    Autonomy.ASK_TO_WRITE: "2 · Ask before changing anything",
    Autonomy.WORKSPACE: "3 · Free inside the working directory",
    Autonomy.FULL: "4 · Full — except system files and installed packages",
}

BLURBS = {
    Autonomy.CONFIRM_ALL: "Every call is confirmed, including reads. Slow, but nothing happens unseen.",
    Autonomy.ASK_TO_WRITE: "Reads run freely. Writing a file or running a command always asks.",
    Autonomy.WORKSPACE: "Edits and commands inside the working directory run without asking. Anything outside it still asks.",
    Autonomy.FULL: "Changes anywhere except /usr, /etc and other system paths, and installed packages in site-packages. "
                   "Editable (developer-mode) installs stay writable — their source is not in site-packages.",
}


@dataclass(frozen=True)
class Decision:
    needs_approval: bool
    reason: str = ""


def _protected(path: Path) -> str:
    """Return a reason string if this path must never be written freely."""
    text = str(path)
    for root in SYSTEM_ROOTS:
        if text == root or text.startswith(root + "/"):
            return f"{root} is a system directory"
    # site-packages holds installed copies. An editable install's real source
    # lives in the repo and is therefore not caught here — intentionally.
    for part in path.parts:
        if part in ("site-packages", "dist-packages"):
            return "installed packages are protected (editable installs are not)"
    return ""


def _targets(arguments: dict, workdir: str) -> list[Path]:
    root = Path(workdir).expanduser().resolve()
    found: list[Path] = []
    for key in PATH_ARGS:
        raw = arguments.get(key)
        if isinstance(raw, str) and raw:
            candidate = Path(raw).expanduser()
            found.append(candidate.resolve() if candidate.is_absolute()
                         else (root / candidate).resolve())
    return found


def _inside(path: Path, workdir: str) -> bool:
    root = Path(workdir).expanduser().resolve()
    return path == root or root in path.parents


def decide(
    tool_name: str,
    arguments: dict,
    workdir: str,
    level: Autonomy,
    tool_requires_approval: bool = False,
) -> Decision:
    """Whether this specific call needs a human decision."""
    if level == Autonomy.CONFIRM_ALL:
        return Decision(True, "level 1 confirms every call")

    if tool_name in READ_ONLY:
        return Decision(False)

    # From here the call changes something.
    if level == Autonomy.ASK_TO_WRITE:
        return Decision(True, "level 2 asks before any change")

    for target in _targets(arguments, workdir):
        why = _protected(target)
        if why:
            return Decision(True, f"{target} — {why}")

    if level == Autonomy.WORKSPACE:
        outside = [p for p in _targets(arguments, workdir) if not _inside(p, workdir)]
        if outside:
            return Decision(True, f"{outside[0]} is outside the working directory")
        # A shell command has no parseable path, so it cannot be shown to stay
        # inside the workspace. Confirm it rather than assume.
        if tool_name in ("run_command", "python_exec"):
            return Decision(True, f"{tool_name} can reach outside the working directory")
        return Decision(False)

    # Autonomy.FULL — protected paths already checked above.
    return Decision(False)


# ------------------------------------------------------------ cloud models
#
# Autonomy needs no provider mapping, and that is worth stating rather than
# leaving implied. Tools execute *here* — `read_file` opens a file on this
# disk, `run_command` runs a shell on this machine — whichever model asked.
# So `decide` is the authority for Claude and GPT exactly as it is for a
# local model, and no cloud API can widen it.
#
# What does change is the consequence of a *read*. A local model that reads a
# file has read a file. A cloud model that reads one has sent its contents to
# a third party, because the result goes back into the next request. That is
# not a reason to require approval for reads — the user chose the provider —
# but it is a reason for the UI to say so plainly and for level 1 to remain
# genuinely useful rather than merely tedious.


def cloud_note(level: Autonomy, provider: str) -> str:
    """What this level means when the model is not running locally."""
    if provider == "local":
        return "Tools run on this machine and nothing leaves it."
    name = {"anthropic": "Anthropic", "openai": "OpenAI"}.get(provider, provider)
    if level == Autonomy.CONFIRM_ALL:
        return (f"Every call is shown before it runs, so nothing reaches {name} "
                f"without you seeing it first.")
    return (f"Tool results are sent to {name} as part of the next request — "
            f"a file this model reads is a file {name} receives.")
