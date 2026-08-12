"""Keeping a conversation inside the model's context window.

A long conversation eventually exceeds the window, and llama.cpp answers that
with an HTTP 400 that says nothing useful. The options are to refuse, to drop
old turns, or to replace them with a summary — and which one is right depends
on the conversation, so it is a setting rather than a policy.

The part that is not negotiable is *what may be dropped together*. A tool call
and its result are one unit: an assistant message saying "I am calling
read_file" whose result has been dropped leaves the model looking at a request
it never got an answer to, and on Anthropic's API it is a hard error — every
``tool_use`` must have a matching ``tool_result`` in the next turn. So history
is compressed in atomic groups, never message by message.

Token counts here are estimates. The exact number depends on the tokenizer,
which differs per model and is not worth a round trip for a decision about when
to start trimming — but the estimate deliberately runs *high*, because
under-counting means the request fails and over-counting only means trimming a
little early.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Iterable


class Strategy(IntEnum):
    KEEP_ALL = 1      # never drop anything; fail when it does not fit
    DROP_OLDEST = 2   # discard the oldest turns
    SUMMARISE = 3     # replace the oldest turns with a summary


#: How much of the usable window to trim *down to* when trimming at all.
#:
#: llama.cpp caches the processed prompt by prefix. Appending to a
#: conversation reuses the whole cache; changing anything early invalidates
#: all of it, and the next request reprocesses the entire context from
#: scratch. On the 122B that is thousands of tokens of prompt processing
#: before a single token of reply.
#:
#: Trimming "just enough to fit" is therefore the worst possible schedule: it
#: pays the full reprocessing cost, and then pays it again next turn, and the
#: turn after that. Trimming to 60% pays it once and buys a long run of
#: append-only turns that all hit the cache.
CACHE_TARGET = 0.6


LABELS = {
    Strategy.KEEP_ALL: "Keep everything",
    Strategy.DROP_OLDEST: "Drop oldest turns",
    Strategy.SUMMARISE: "Summarise oldest turns",
}

BLURBS = {
    Strategy.KEEP_ALL: "Nothing is ever discarded. The request fails when the "
                       "conversation no longer fits, and you decide what to do.",
    Strategy.DROP_OLDEST: "Oldest turns are discarded to make room. Fast and "
                          "lossy — the model simply stops knowing what was said.",
    Strategy.SUMMARISE: "Oldest turns are replaced with a summary the model "
                        "writes. Keeps the gist, costs an extra generation.",
}

#: Characters per token. English prose runs about 4; code and JSON run denser,
#: nearer 3. The lower number is used so the estimate errs high — trimming
#: early is a small cost, and under-counting is a failed request.
CHARS_PER_TOKEN = 3.2

#: Per-message overhead for role markers and template scaffolding.
MESSAGE_OVERHEAD = 8


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def message_tokens(message: dict[str, Any]) -> int:
    """Rough size of one message, tool calls included."""
    total = MESSAGE_OVERHEAD
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif content:
        total += estimate_tokens(json.dumps(content))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += estimate_tokens(function.get("name", ""))
        total += estimate_tokens(function.get("arguments", ""))
        total += MESSAGE_OVERHEAD
    return total


def total_tokens(messages: Iterable[dict[str, Any]]) -> int:
    return sum(message_tokens(m) for m in messages)


@dataclass(frozen=True)
class Budget:
    """How much room the conversation actually has.

    The window is not all available: the system prompt, the enabled skills and
    the tool schemas are re-sent on every request, and the reply needs
    somewhere to go. What is left over is the conversation's share, and with
    every skill enabled on a 16K model that share has been negative — which
    surfaced as an unexplained HTTP 400 before a word was typed.
    """

    limit: int
    preamble: int = 0
    reserve_output: int = 2048

    @property
    def available(self) -> int:
        return max(0, self.limit - self.preamble - self.reserve_output)

    def used_fraction(self, messages) -> float:
        return (total_tokens(messages) / self.available) if self.available else 1.0

    def fits(self, messages) -> bool:
        return total_tokens(messages) <= self.available


def groups(history: list[dict[str, Any]]) -> list[list[int]]:
    """Indices of messages that must be kept or dropped together.

    An assistant turn that calls tools owns every tool result that follows it.
    Splitting them leaves the model with an unanswered request, and Anthropic
    rejects it outright: every ``tool_use`` needs a matching ``tool_result``.
    """
    out: list[list[int]] = []
    for index, message in enumerate(history):
        role = message.get("role")
        if role == "tool" and out:
            out[-1].append(index)
        else:
            out.append([index])
    return out


@dataclass
class Result:
    """What compression did, so the UI can say so rather than silently losing turns."""

    history: list[dict[str, Any]]
    dropped: int = 0
    summary: str = ""
    note: str = ""
    overflowed: bool = False


def compress(
    history: list[dict[str, Any]],
    budget: Budget,
    strategy: Strategy = Strategy.DROP_OLDEST,
    summarise: Callable[[list[dict[str, Any]]], str] | None = None,
    keep_recent: int = 2,
    cache_target: float = CACHE_TARGET,
) -> Result:
    """Fit ``history`` into ``budget``, in whole turns.

    ``keep_recent`` groups at the end are never dropped, whatever the
    strategy: a conversation trimmed to nothing is not a conversation, and the
    last exchange is the one the next message refers to.

    ``cache_target`` is why this trims further than it needs to. See
    :data:`CACHE_TARGET`: trimming to exactly fit invalidates the prompt cache
    and then does it again next turn, and the turn after. Trimming once, hard,
    buys a run of append-only turns that all hit the cache.
    """
    if budget.fits(history):
        return Result(list(history))

    if strategy == Strategy.KEEP_ALL:
        return Result(
            list(history), overflowed=True,
            note=(f"The conversation is about {total_tokens(history):,} tokens and "
                  f"only {budget.available:,} fit. Start a new conversation, or "
                  f"change Context handling in the sidebar."),
        )

    blocks = groups(history)
    keep_from = max(0, len(blocks) - keep_recent)
    dropped_indices: list[int] = []

    # Not "until it fits" but "until it is comfortably under": every trim
    # costs a full prompt reprocess, so the thing to minimise is how often
    # one happens, not how much is kept on the turn it does.
    target = max(1, int(budget.available * max(0.1, min(1.0, cache_target))))

    cut = 0
    while cut < keep_from:
        candidate = [i for block in blocks[cut + 1:] for i in block]
        remaining = [history[i] for i in candidate]
        dropped_indices = [i for block in blocks[:cut + 1] for i in block]
        cut += 1
        if total_tokens(remaining) <= target:
            break
    else:
        # Could not reach the target without eating the recent tail. Settle
        # for whatever fits, which is the old behaviour.
        pass

    kept = [history[i] for i in range(len(history)) if i not in set(dropped_indices)]
    dropped = len(dropped_indices)
    if not dropped:
        return Result(
            list(history), overflowed=True,
            note=("Even the most recent exchange does not fit in this context "
                  "window. Raise the context size, or send a shorter message."),
        )

    if strategy == Strategy.SUMMARISE and summarise is not None:
        removed = [history[i] for i in dropped_indices]
        try:
            text = (summarise(removed) or "").strip()
        except Exception:  # noqa: BLE001 - a failed summary must not lose the turn
            text = ""
        if text:
            marker = {
                "role": "user",
                "content": (
                    "[Earlier in this conversation, summarised to save context]\n"
                    + text
                ),
            }
            # Inserted as a user turn rather than a system one: a second system
            # message is not representable on Anthropic, which takes exactly one.
            kept = [marker] + kept
            return Result(kept, dropped, text,
                          note=f"Summarised {dropped} earlier message(s) to save room.")

    headroom = budget.available - total_tokens(kept)
    return Result(kept, dropped, note=(
        f"Dropped {dropped} earlier message(s), leaving {headroom:,} tokens "
        f"free. Trimmed further than needed so the next few turns reuse the "
        f"prompt cache instead of reprocessing it."))


SUMMARY_PROMPT = (
    "Summarise the conversation below so it can replace the original and "
    "still be useful. Keep: decisions made, facts established, file paths, "
    "names, numbers, and anything the user asked for that is not done yet. "
    "Drop: pleasantries, and reasoning that led nowhere. Write it as notes, "
    "not prose, and do not add anything that is not there."
)


def summary_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request that asks a model to summarise the span being dropped."""
    transcript = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            transcript.append(f"{role}: {content}")
        for call in message.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name", "?")
            transcript.append(f"{role}: [called {name}]")
    return [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": "\n\n".join(transcript)[:60_000]},
    ]
