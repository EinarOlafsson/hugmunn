"""The loop tries strategies instead of stopping at a round count.

Reported: "I keep getting stopped after 12 rounds without a final answer... it
should try many different strategies before reaching this point." Then, more
precisely: try something, change something, try again — and every ten rounds
or so, step back, re-canvas, learn more, try again.

Two failures were in play. Twelve rounds is far too few for work that reads
several files or retries a failed route. And the behaviour *at* the ceiling was
to discard everything the run had learned and report a round count, which is
the worst available outcome: the tools ran, the results are in the history, and
the user gets arithmetic.
"""

from __future__ import annotations

import pytest

from localagent.core import agent as agentkit
from localagent.core.agent import Agent
from localagent.core.client import Event


class _Call:
    def __init__(self, name="read_file", arguments=None):
        self.id = "c"
        self.name = name
        self._args = arguments or {"path": "/tmp/x"}

    @property
    def arguments(self):
        import json

        return json.dumps(self._args)

    def parsed_arguments(self):
        return self._args


class Relentless:
    """A model that only ever calls tools, never answering.

    Its calls *vary*, because an identical repeat is caught by a check that
    fires first — correctly, since repeating is the more urgent problem — and
    a fake that repeats never exercises anything else.
    """

    def __init__(self):
        self.turns = 0
        self.saw_tools = []
        self.received = []

    def is_ready(self):
        return True

    def stream(self, messages, tools=None, temperature=None, max_tokens=4096,
               cancel=None, thinking=None):
        self.turns += 1
        self.saw_tools.append(tools is not None)
        self.received.append([m.get("content") for m in messages])
        if tools is None:
            # Tools withheld: the only move left is to answer.
            yield Event("content", text="Here is what I found and where I stopped.")
        else:
            yield Event("tool_calls",
                        tool_calls=[_Call(arguments={"path": f"/tmp/{self.turns}"})])
        yield Event("done")


def run(client, **kwargs):
    agent = Agent(client=client, workdir="/tmp", system_prompt="", **kwargs)
    return list(agent.run([{"role": "user", "content": "go"}],
                          approve=lambda *a: True))


# ---------------------------------------------------------- the round limit


def test_the_default_budget_is_sized_for_work_not_one_question():
    from localagent.config import Settings

    assert Settings().max_tool_iterations >= 40


def test_hitting_the_ceiling_produces_an_answer_not_an_error():
    """The reported symptom. Everything the run learned is in the history;
    reporting a round count throws it away."""
    client = Relentless()
    events = run(client, max_iterations=6)

    kinds = [e.kind for e in events]
    assert kinds[-1] == "done", "a run that hits the ceiling must still answer"
    assert "error" not in kinds
    assert any(e.kind == "content" and "found" in e.text for e in events)


def test_the_final_pass_withholds_tools():
    """Otherwise the model calls another one and the ceiling means nothing."""
    client = Relentless()
    run(client, max_iterations=5)
    assert client.saw_tools[-1] is False
    assert all(client.saw_tools[:-1])


def test_the_answer_reaches_the_history():
    client = Relentless()
    history = [{"role": "user", "content": "go"}]
    agent = Agent(client=client, workdir="/tmp", system_prompt="", max_iterations=4)
    list(agent.run(history, approve=lambda *a: True))
    assert history[-1]["role"] == "assistant"
    assert "found" in history[-1]["content"]


def test_the_user_is_told_the_budget_ran_out():
    """Silently switching to a summary would look like a normal answer."""
    events = run(Relentless(), max_iterations=4)
    assert any(e.kind == "notice" and "rounds" in e.text for e in events)


# -------------------------------------------------------------- strategies


def agent(max_iterations=40):
    return Agent(client=None, workdir="/tmp", system_prompt="",
                 max_iterations=max_iterations)


def test_a_run_that_is_going_fine_is_left_alone():
    """Advice injected into a working loop is noise to dismiss."""
    assert agent()._nudge(3, [("read_file", "a"), ("search_text", "b")]) == ""


def test_an_exact_repeat_is_interrupted_immediately():
    nudge = agent()._nudge(4, [("run_command", "pytest")] * 2)
    assert "same call" in nudge
    assert "pytest" in nudge


def test_a_failure_asks_for_one_change_rather_than_a_retry():
    nudge = agent()._nudge(4, [("run_command", "pytest")], failures=1)
    assert "Change one thing" in nudge


def test_the_step_back_happens_on_a_cycle_not_once():
    """A long run should re-canvas repeatedly — the second one is often where
    the wrong assumption from the first is finally noticed."""
    subject = agent(max_iterations=60)
    tried = [("read_file", "a"), ("search_text", "b")]
    stepped = [i for i in range(1, 55) if "Re-canvas" in subject._nudge(i, tried)
               or "re-canvas" in subject._nudge(i, tried).lower()]
    assert stepped == [10, 20, 30, 40, 50]


def test_the_step_back_forbids_attempting_a_fix_in_that_round():
    """Told merely to 'consider gathering information', a model gathers one
    fact and immediately tries again — the behaviour being interrupted."""
    nudge = agent()._nudge(10, [("read_file", "a"), ("search_text", "b")])
    assert "Do not try another fix" in nudge


def test_the_step_back_asks_what_was_assumed_without_checking():
    nudge = agent()._nudge(10, [("read_file", "a"), ("search_text", "b")])
    assert "ASSUMED" in nudge
    assert "NOT looked at" in nudge


def test_it_converges_near_the_ceiling_instead_of_stepping_back():
    """Re-canvassing with two rounds left wastes them."""
    subject = agent(max_iterations=22)
    late = subject._nudge(20, [("read_file", "a"), ("search_text", "b")])
    assert "Re-canvas" not in late


def test_the_converge_message_names_the_budget():
    nudge = agent(max_iterations=40)._nudge(34, [("read_file", "a"), ("x", "y")])
    assert "34" in nudge and "40" in nudge


@pytest.mark.parametrize("iteration", range(1, 40))
def test_no_nudge_ever_raises(iteration):
    subject = agent()
    for attempted in ([], [("a", "b")], [("a", "b"), ("a", "b")]):
        assert isinstance(subject._nudge(iteration, attempted), str)


def test_nudges_reach_the_model_as_conversation_not_configuration():
    """A second system message is not representable on Anthropic, and a model
    treats a system turn as configuration rather than as something to act on."""
    client = Relentless()
    run(client, max_iterations=13)
    injected = [c for turn in client.received for c in turn
                if isinstance(c, str) and "Re-canvas" in c]
    assert injected, "the step back should have been sent"


def test_the_recanvas_interval_is_what_was_asked_for():
    assert agentkit.RECANVAS_EVERY == 10
