"""How hard the agent keeps going before it stops.

Distinct from effort, which is about how carefully the model thinks inside one
answer. This is about the loop: how many tool rounds it may spend, how often it
is made to stop and re-canvas, and what it does when the budget runs out.

The two are genuinely independent. A quick question answered thoroughly is
effort 3, persistence 1. A tedious migration across forty files is the
reverse — no deep thinking per step, but it must not stop at step twelve and
report a round count, which is exactly the failure this tier exists to fix.

Persistence owns the round budget outright. There is no separate
"max iterations" setting: two controls over one decision is what made the
autonomy tier appear broken, and the same mistake twice would be a choice.
"""

from __future__ import annotations

from enum import IntEnum


class Persistence(IntEnum):
    """Tool-round budgets: LIGHT 8, NORMAL 25, PERSISTENT 60, RELENTLESS 200."""

    LIGHT = 1
    NORMAL = 2
    PERSISTENT = 3
    RELENTLESS = 4


LABELS = {
    Persistence.LIGHT: "1 · Light",
    Persistence.NORMAL: "2 · Normal",
    Persistence.PERSISTENT: "3 · Persistent",
    Persistence.RELENTLESS: "4 · Relentless",
}

#: Tool rounds allowed per turn. The old default was 12, which is fine for a
#: question and far too few for work: reading four files, running the tests,
#: reading the failure and fixing it spends that before anything is achieved.
MAX_ROUNDS = {
    Persistence.LIGHT: 8,
    Persistence.NORMAL: 25,
    Persistence.PERSISTENT: 60,
    Persistence.RELENTLESS: 200,
}

#: Rounds of attempt-and-adjust before the loop is forced to stop and gather
#: instead. Tighter at higher tiers, not looser: a run permitted two hundred
#: rounds needs *more* interruption, or it spends them all down one wrong
#: assumption made in the first five.
RECANVAS_EVERY = {
    Persistence.LIGHT: 6,
    Persistence.NORMAL: 10,
    Persistence.PERSISTENT: 10,
    Persistence.RELENTLESS: 8,
}

BLURBS = {
    Persistence.LIGHT: "Up to 8 tool rounds. Stops early and says what it "
                       "found. For questions, not for work.",
    Persistence.NORMAL: "Up to 25 rounds, stepping back every 10 to re-check "
                        "its assumptions. The sensible default.",
    Persistence.PERSISTENT: "Up to 60 rounds. Will retry failed routes and "
                            "change approach rather than reporting a blocker.",
    Persistence.RELENTLESS: "Up to 200 rounds, re-canvassing every 8. Stops "
                            "only when the objective is met, genuinely "
                            "blocked, or it needs a decision that is yours.",
}

_LIGHT = """
Answer with as few tool calls as you can. If two or three do not settle it,
say what you found and what is still open rather than continuing to dig.
""".strip()

_NORMAL = """
Work the problem until it is answered. When something fails, change one thing
and try again; when two variations have failed, change the assumption
underneath rather than the details.
""".strip()

_PERSISTENT = """
Keep going. A failed tool call is information about the route, not a reason to
stop — read the error, work out what it implies, and take a different one.

Report a blocker only when it is genuinely outside what you can do here. "This
is harder than expected" is not a blocker. Neither is "there are several
possibilities": narrow them.
""".strip()

_RELENTLESS = """
Do not stop until the work is actually done.

Not when it becomes tedious, not when the obvious route fails, and not when
the answer is getting long. If an approach fails twice, the approach is wrong
— go back, gather what you skipped, and take a different one. Repeat that as
many times as it takes.

Three things, and only these three, end the turn early:

* the objective is met, and you have verified it rather than assumed it;
* you need a decision that is the user's to make, and you ask for it plainly;
* continuing would do something destructive that was not asked for.

Everything else is work to be done. When you are tempted to summarise progress
and stop, that is the point at which to keep going instead.
""".strip()

BODIES = {
    Persistence.LIGHT: _LIGHT,
    Persistence.NORMAL: _NORMAL,
    Persistence.PERSISTENT: _PERSISTENT,
    Persistence.RELENTLESS: _RELENTLESS,
}


def instructions(level: Persistence) -> str:
    return f'<persistence level="{LABELS[level]}">\n{BODIES[level]}\n</persistence>'


def max_rounds(level: Persistence) -> int:
    return MAX_ROUNDS[level]


def recanvas_every(level: Persistence) -> int:
    return RECANVAS_EVERY[level]


def summary(level: Persistence) -> str:
    return (f"{MAX_ROUNDS[level]} tool rounds, "
            f"re-canvassing every {RECANVAS_EVERY[level]}")
