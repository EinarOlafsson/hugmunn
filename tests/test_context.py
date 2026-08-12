"""Fitting a conversation into a context window without corrupting it.

The dangerous part is not deciding *when* to trim — it is deciding what may be
removed together. An assistant turn that called a tool owns the result that
follows it, and separating them leaves the model looking at a request it never
got an answer to. Anthropic rejects that outright: every ``tool_use`` needs a
matching ``tool_result``. So these lean hard on the grouping.
"""

from __future__ import annotations

import pytest

from hugmunn.core.context import (
    Budget, Result, Strategy, compress, groups, message_tokens,
    summary_request, total_tokens,
)


def user(text):
    return {"role": "user", "content": text}


def assistant(text=None, calls=()):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": "{}"}}
            for cid, name in calls
        ]
    return message


def tool_result(cid, text="ok"):
    return {"role": "tool", "tool_call_id": cid, "content": text}


# ------------------------------------------------------------- estimation


def test_a_longer_message_estimates_larger():
    assert message_tokens(user("hi")) < message_tokens(user("hi " * 500))


def test_the_estimate_errs_high_rather_than_low():
    """Under-counting means a failed request; over-counting means trimming early."""
    text = "the quick brown fox jumps over the lazy dog. " * 20
    words = len(text.split())
    # A real tokenizer lands near one token per word for plain English; the
    # estimate must not come in under that.
    assert message_tokens(user(text)) >= words


def test_tool_calls_are_counted():
    plain = assistant("done")
    calling = assistant("done", calls=[("c1", "read_file")])
    assert message_tokens(calling) > message_tokens(plain)


def test_a_none_content_does_not_raise():
    assert message_tokens(assistant(None, calls=[("c1", "f")])) > 0


# ---------------------------------------------------------------- budget


def test_the_preamble_and_the_reply_come_out_of_the_window():
    """Skills and tool schemas are re-sent every request; the reply needs room."""
    budget = Budget(limit=16384, preamble=12000, reserve_output=2048)
    assert budget.available == 16384 - 12000 - 2048


def test_a_preamble_larger_than_the_window_leaves_nothing():
    """With every skill on, a 16K model has had a negative share."""
    assert Budget(limit=16384, preamble=20000).available == 0


# --------------------------------------------------------------- grouping


def test_a_tool_call_and_its_result_are_one_group():
    history = [user("read it"), assistant(None, [("c1", "read_file")]),
               tool_result("c1")]
    assert groups(history) == [[0], [1, 2]]


def test_several_results_join_the_call_that_made_them():
    history = [
        user("read both"),
        assistant(None, [("c1", "read_file"), ("c2", "read_file")]),
        tool_result("c1"), tool_result("c2"),
    ]
    assert groups(history) == [[0], [1, 2, 3]]


def test_plain_turns_are_their_own_groups():
    history = [user("a"), assistant("b"), user("c")]
    assert groups(history) == [[0], [1], [2]]


# ------------------------------------------------------------ compression


def test_a_conversation_that_fits_is_returned_untouched():
    history = [user("hello"), assistant("hi")]
    result = compress(history, Budget(limit=10_000), Strategy.DROP_OLDEST)
    assert result.history == history
    assert result.dropped == 0


def test_keep_all_refuses_rather_than_losing_anything():
    history = [user("x" * 4000), assistant("y" * 4000), user("now")]
    result = compress(history, Budget(limit=500, reserve_output=100),
                      Strategy.KEEP_ALL)
    assert result.overflowed
    assert result.history == history       # nothing lost
    assert "Context handling" in result.note


def test_dropping_removes_from_the_front():
    history = [user(f"message {i} " + "x" * 900) for i in range(8)]
    result = compress(history, Budget(limit=2000, reserve_output=200),
                      Strategy.DROP_OLDEST)
    assert result.dropped > 0
    assert result.history[-1] is history[-1]     # the newest survives
    assert history[0] not in result.history      # the oldest does not


def test_a_tool_result_is_never_orphaned_from_its_call():
    """The failure this whole module is arranged around."""
    history = [
        user("old " + "x" * 3000),
        assistant(None, [("c1", "read_file")]),
        tool_result("c1", "y" * 3000),
        user("new question"),
        assistant("answer"),
    ]
    result = compress(history, Budget(limit=1200, reserve_output=200),
                      Strategy.DROP_OLDEST)
    call_ids = {c["id"] for m in result.history for c in m.get("tool_calls") or []}
    result_ids = {m["tool_call_id"] for m in result.history if m.get("role") == "tool"}
    assert call_ids == result_ids, "a tool call and its result must move together"


def test_the_most_recent_exchange_is_never_dropped():
    """A conversation trimmed to nothing is not a conversation."""
    history = [user("x" * 2000) for _ in range(6)]
    result = compress(history, Budget(limit=300, reserve_output=100),
                      Strategy.DROP_OLDEST, keep_recent=2)
    assert len(result.history) >= 2


def test_an_impossible_single_turn_is_reported_not_silently_mangled():
    history = [user("x" * 100_000)]
    result = compress(history, Budget(limit=500, reserve_output=100),
                      Strategy.DROP_OLDEST)
    assert result.overflowed
    assert "Raise the context size" in result.note


# ------------------------------------------------------------ summarising


def test_summarising_replaces_the_dropped_span():
    history = [user(f"turn {i} " + "x" * 900) for i in range(8)]
    result = compress(history, Budget(limit=2000, reserve_output=200),
                      Strategy.SUMMARISE,
                      summarise=lambda msgs: "user asked about turns 0-4")
    assert result.summary
    assert "summarised" in result.history[0]["content"].lower()
    assert "turns 0-4" in result.history[0]["content"]


def test_the_summary_goes_in_as_a_user_turn_not_a_second_system_one():
    """Anthropic takes exactly one system prompt, as a top-level field."""
    history = [user(f"turn {i} " + "x" * 900) for i in range(8)]
    result = compress(history, Budget(limit=2000, reserve_output=200),
                      Strategy.SUMMARISE, summarise=lambda m: "notes")
    assert result.history[0]["role"] == "user"
    assert not any(m.get("role") == "system" for m in result.history)


def test_a_failed_summary_falls_back_to_dropping():
    """A summariser that raises must not take the conversation down with it."""
    history = [user(f"turn {i} " + "x" * 900) for i in range(8)]

    def broken(_):
        raise RuntimeError("the model is not running")

    result = compress(history, Budget(limit=2000, reserve_output=200),
                      Strategy.SUMMARISE, summarise=broken)
    assert result.dropped > 0
    assert not result.summary
    assert "Dropped" in result.note


def test_an_empty_summary_falls_back_to_dropping():
    history = [user(f"turn {i} " + "x" * 900) for i in range(8)]
    result = compress(history, Budget(limit=2000, reserve_output=200),
                      Strategy.SUMMARISE, summarise=lambda m: "   ")
    assert result.dropped > 0 and not result.summary


def test_the_summary_request_carries_the_transcript_and_the_instruction():
    history = [user("what is in config.py"), assistant("a registry"),
               assistant(None, [("c1", "read_file")]), tool_result("c1")]
    request = summary_request(history)
    assert request[0]["role"] == "system"
    body = request[1]["content"]
    assert "config.py" in body
    assert "read_file" in body       # tool calls are worth keeping in the notes


def test_the_summary_request_is_bounded():
    """The span being summarised is by definition too big to send whole."""
    history = [user("x" * 500_000)]
    assert len(summary_request(history)[1]["content"]) <= 60_000


# --------------------------------------------------------- every strategy


@pytest.mark.parametrize("strategy", list(Strategy))
def test_every_strategy_returns_a_usable_history(strategy):
    history = [user("hello " + "x" * 900), assistant("hi"), user("again")]
    result = compress(history, Budget(limit=800, reserve_output=100), strategy,
                      summarise=lambda m: "notes")
    assert isinstance(result, Result)
    assert result.history
    assert all(m.get("role") for m in result.history)


@pytest.mark.parametrize("strategy", list(Strategy))
def test_every_strategy_has_a_label_and_an_explanation(strategy):
    from hugmunn.core.context import BLURBS, LABELS

    assert LABELS[strategy] and len(BLURBS[strategy]) > 40
