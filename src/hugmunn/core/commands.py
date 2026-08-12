"""Slash commands typed into the composer.

Things that are settings but do not feel like settings. Turning on remote
access is a decision made in the middle of doing something else — you are at
the desk, you are about to leave, you want it on your phone — and making that
a trip to a dialog means it does not get used.

They are resolved *before* the message reaches the model. A line beginning
with a slash is never sent: if a command is not recognised it says so rather
than asking a model to interpret it, because a model asked to interpret
``/remote`` will cheerfully explain what it thinks the word means.

``/goal`` is the exception that changes the turn rather than replacing it. It
attaches an objective the agent works toward across tool rounds instead of
stopping at the first plausible answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Command:
    name: str
    argument: str          # "" when it takes none; a hint otherwise
    summary: str
    detail: str = ""

    @property
    def usage(self) -> str:
        return f"/{self.name}" + (f" {self.argument}" if self.argument else "")


COMMANDS: tuple[Command, ...] = (
    Command("help", "", "List these commands."),
    Command("remote", "[on|off|url]",
            "Expose hugmunn to another device.",
            "Starts a local web server and prints the address. Nothing is "
            "reachable from outside this machine until you also run a tunnel; "
            "the reply says how. A token is always required."),
    Command("goal", "<objective>",
            "Work toward an objective until it is met.",
            "The agent keeps going across tool rounds rather than stopping at "
            "the first plausible answer, and reports whether it actually got "
            "there. Clear it with /goal off."),
    Command("model", "[name]",
            "Switch model, or list what is available."),
    Command("autonomy", "[1-4]",
            "Show or set what may run without asking."),
    Command("effort", "[1-4]",
            "Show or set how hard the model works before answering."),
    Command("persistence", "[1-4|relentless]",
            "How long the loop keeps going before it stops.",
            "Separate from effort: effort is how carefully it thinks inside "
            "one answer, persistence is how many tool rounds it may spend and "
            "how often it is made to step back and re-canvas. Relentless "
            "stops only when the work is done, genuinely blocked, or waiting "
            "on a decision that is yours."),
    Command("think", "[on|off]",
            "Whether the model reasons before answering.",
            "Qwen3 templates default this on, and on the abliterated builds "
            "having it on brings refusals back."),
    Command("context", "[tokens]",
            "Show or set the context window for this model."),
    Command("theme", "[name]",
            "Switch theme, or list them."),
    Command("clear", "", "Start a new conversation."),
    Command("save", "", "Write the conversation now, and say where."),
    Command("stop", "", "Cancel the turn in flight."),
)

BY_NAME = {c.name: c for c in COMMANDS}


#: A command name: a letter, then letters, digits or hyphens. Deliberately
#: excludes ``/`` and ``.``, which is what separates a command from a path.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def is_command(text: str) -> bool:
    """A line whose first word is a slash followed by a bare name.

    ``/usr/local/bin/llama-server`` is not a command and neither is ``/2``:
    pasting a path or a fragment as the whole message is a normal thing to do,
    and answering it with "did you mean /remote?" would be worse than useless.
    """
    stripped = (text or "").lstrip()
    if len(stripped) < 2 or stripped[0] != "/":
        return False
    first = stripped[1:].split()[0] if stripped[1:].split() else ""
    return bool(_NAME.match(first))


def parse(text: str) -> tuple[str, str]:
    """``"/goal find the leak"`` -> ``("goal", "find the leak")``."""
    stripped = (text or "").lstrip()[1:]
    name, _, argument = stripped.partition(" ")
    return name.strip().lower(), argument.strip()


def help_text() -> str:
    width = max(len(c.usage) for c in COMMANDS)
    lines = ["Commands are handled here and never sent to the model.", ""]
    for command in COMMANDS:
        lines.append(f"  {command.usage.ljust(width)}   {command.summary}")
    return "\n".join(lines)


def detail_for(name: str) -> str:
    command = BY_NAME.get(name)
    if command is None:
        return ""
    return f"{command.usage}\n{command.summary}" + (
        f"\n\n{command.detail}" if command.detail else "")


def unknown(name: str) -> str:
    """What to say for a slash that is not a command.

    Suggests the nearest match rather than only refusing: the common case is
    a typo, and a bare "unknown command" makes the user go and read a list.
    """
    import difflib

    close = difflib.get_close_matches(name, list(BY_NAME), n=1, cutoff=0.6)
    suggestion = f"  Did you mean /{close[0]}?\n" if close else ""
    return (f"/{name} is not a command.\n{suggestion}"
            f"  /help lists them. To send a line that starts with a slash, "
            f"put a space before it.")


# --------------------------------------------------------------------- goal
#
# Not a setting on the model but an instruction to the agent loop, so it lives
# with the effort text rather than in the registry.

GOAL_INSTRUCTIONS = """
<goal>
{objective}
</goal>

You are working toward that objective, not answering a question about it.

Keep going until it is actually met. A partial result, a plan for how it might
be met, or a description of what you would do are none of them the objective.
If a tool call fails, read the error and try a different route rather than
reporting the failure and stopping.

Before you finish, check the objective against what you have actually done and
say plainly which it is:

* **Met** — say how you verified it, not that you believe it.
* **Blocked** — say exactly what stopped you and what would unblock it. This
  is a real outcome and reporting it early is better than a long detour.
* **Partly met** — say which part, and what is left.

Do not stop merely because the answer has become long. Do stop if continuing
would need a decision that is the user's to make, and ask for that decision.
""".strip()


def goal_block(objective: str) -> str:
    return GOAL_INSTRUCTIONS.format(objective=objective.strip())


#: How many tool rounds a goal is allowed before the loop gives up. Higher
#: than the normal limit because working toward something is the point, and
#: the normal limit is sized for a single question.
GOAL_MAX_ITERATIONS = 40


@dataclass
class Outcome:
    """What a command did, and what the UI should do next."""

    message: str = ""              # shown in the transcript
    handled: bool = True           # False means send it to the model after all
    send_text: str = ""            # a rewritten message to send instead
    refresh: bool = False           # controls changed; re-read them


Handler = Callable[[str], Outcome]
