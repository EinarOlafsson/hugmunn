"""Effort tiers: how hard the model works before answering.

Each tier trades time and tokens for a lower chance of being wrong. The text is
injected into the system prompt like a skill, but effort is a *selector* rather
than a checkbox — the tiers are mutually exclusive, and exactly one is always
active.

Tier 4 additionally grants the ``spawn_agent`` tool. The reason verification is
gated behind the top tier rather than always available is that a subagent costs
a full extra generation pass; on a 37 tok/s local model that is a real minute,
not a rounding error.
"""

from __future__ import annotations

from enum import IntEnum


class Effort(IntEnum):
    """Reasoning/instruction level from QUICK (1) to EXHAUSTIVE (4)."""

    QUICK = 1
    STANDARD = 2
    THOROUGH = 3
    EXHAUSTIVE = 4


LABELS = {
    Effort.QUICK: "1 · Quick",
    Effort.STANDARD: "2 · Standard",
    Effort.THOROUGH: "3 · Thorough",
    Effort.EXHAUSTIVE: "4 · Exhaustive (subagents)",
}

BLURBS = {
    Effort.QUICK: "Answer directly. Minimal tool use, no verification. Fastest, least reliable.",
    Effort.STANDARD: "Normal working mode. Use tools when the answer depends on them; sanity-check the result.",
    Effort.THOROUGH: "Verify every factual claim with a tool. Re-read after editing, run the tests, state confidence.",
    Effort.EXHAUSTIVE: "Everything in Thorough, plus independent subagents that re-derive the answer and try to refute it. "
                       "Several times slower.",
}

_QUICK = """
Answer directly and briefly. Reach for a tool only when the answer is
impossible without one — do not read a file to confirm something already
established, and do not search to check a fact you are confident about.

Do not verify your own work at this level. If you are uncertain, say so in one
clause rather than spending a tool call resolving it.

Keep the response to what was asked. No preamble, no summary of what you are
about to do, no offer of further help.
""".strip()

_STANDARD = """
Work normally. Use a tool whenever the answer depends on something you cannot
see — file contents, command output, current information — and use the result
rather than your prior assumption.

Read a file before editing it. Check that a command succeeded before building
on it. If a tool returns an error, read it and adapt rather than retrying
unchanged.

Sanity-check the result before reporting: does the number have a plausible
magnitude, does the edit appear where you intended, does the count match what
you expected. One check, not an audit.
""".strip()

_THOROUGH = """
Verify rather than assume. Every factual claim in your answer should trace to
something you retrieved this session — a file you read, a command you ran, a
search result. If a claim cannot be traced, mark it as an assumption.

After any edit, read the file back and confirm the change is present and
correct. After any code change, run the tests. Report the actual output rather
than asserting success.

Check the boundaries of what you did: the case where input is empty, the path
that is not taken, the second occurrence you may have missed. Most wrong
answers at this level come from a case that was never considered rather than a
step done badly.

Re-read your own answer before sending, as a reviewer would. State your
confidence where it is not high, and say explicitly what you did not check.

Expect to use two or three times the tool calls of Standard. That is the point.
""".strip()

_EXHAUSTIVE = """
Apply everything in Thorough, then verify independently.

For any consequential conclusion — a diagnosis, a numeric result, a claim that
something is correct — use `spawn_agent` to have a fresh agent re-derive it
without seeing your reasoning. Independent derivation catches errors that
re-reading your own work does not, because you re-read with the same
assumptions that produced the error.

Brief each subagent precisely and narrowly. One question, the context needed to
answer it, and nothing about the answer you expect — an agent told what you
concluded will tend to confirm it. Grant only the tools that question needs;
a verification agent almost never needs to write or execute anything.

When a subagent disagrees with you, do not simply average. Find which of you is
wrong and say which, with the evidence. If it cannot be resolved, report both
positions and the disagreement rather than picking one silently.

Also use a subagent when a subtask is genuinely independent and would otherwise
crowd your context — reading a long file to extract one fact, checking a
reference, summarising something you only need the conclusion from.

Do not spawn agents for work you could finish in a couple of tool calls
yourself, and do not spawn several for one small task. Each costs a full
generation pass, which on a local model is measured in minutes.
""".strip()

BODIES = {
    Effort.QUICK: _QUICK,
    Effort.STANDARD: _STANDARD,
    Effort.THOROUGH: _THOROUGH,
    Effort.EXHAUSTIVE: _EXHAUSTIVE,
}


def instructions(level: Effort) -> str:
    return f"<effort level=\"{LABELS[level]}\">\n{BODIES[level]}\n</effort>"


def grants_subagents(level: Effort) -> bool:
    return level >= Effort.EXHAUSTIVE


# ------------------------------------------------- the same tier, in the cloud
#
# A local model has no dial for this, so the tier is entirely prompt: the text
# above is what makes tier 3 slower and more careful than tier 1. Claude and
# GPT both have a real one, and using it means the tier buys actual extra
# computation rather than only a change of instructions.
#
# The prompt text is still sent. The two are complementary — the budget decides
# how long the model may think, the instructions decide what to do with the
# time — and a model given 32K thinking tokens with no direction spends them
# rehearsing the question.


#: Thinking tokens per tier for Anthropic's extended thinking.
#:
#: 1024 is the API's floor, so tier 1 turns it off rather than asking for a
#: budget that would be rejected. The budget is spent from the same allowance
#: as the visible answer, which is why the client raises ``max_tokens`` to
#: clear it — asking for 32K of thinking inside a 32K limit leaves no room to
#: reply, and the turn ends mid-thought with nothing shown.
ANTHROPIC_BUDGET = {
    Effort.QUICK: 0,
    Effort.STANDARD: 4_096,
    Effort.THOROUGH: 16_384,
    Effort.EXHAUSTIVE: 32_768,
}

#: The nearest equivalent on OpenAI's four-step ``reasoning_effort``.
OPENAI_EFFORT = {
    Effort.QUICK: "minimal",
    Effort.STANDARD: "low",
    Effort.THOROUGH: "medium",
    Effort.EXHAUSTIVE: "high",
}


def anthropic_budget(level: Effort) -> int:
    """Extended-thinking budget in tokens. 0 disables thinking."""
    return ANTHROPIC_BUDGET[level]


def openai_effort(level: Effort) -> str:
    return OPENAI_EFFORT[level]


def cloud_note(level: Effort, provider: str) -> str:
    """One line for the sidebar saying what the tier does on this provider."""
    if provider == "anthropic":
        budget = anthropic_budget(level)
        return ("Thinking off." if not budget
                else f"Up to {budget:,} thinking tokens per turn.")
    if provider == "openai":
        return f"reasoning_effort = {openai_effort(level)}."
    return "Prompt-only on local models — they have no thinking dial."
