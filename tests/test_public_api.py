"""The public surface: ``import hugmunn`` and nothing deeper.

Everything here is a promise. A name in ``hugmunn.__all__`` keeps its meaning
across a minor version, so these tests exist as much to make a change
deliberate as to catch a mistake.
"""

from __future__ import annotations

import threading

import pytest

import hugmunn


# ------------------------------------------------------------- the surface


def test_importing_the_package_is_enough():
    """A caller should never need hugmunn.core."""
    for name in ("Agent", "agent", "Event", "Model", "models", "sign_in"):
        assert hasattr(hugmunn, name), name


def test_everything_exported_exists():
    missing = [n for n in hugmunn.__all__ if not hasattr(hugmunn, n)]
    assert not missing


def test_the_tiers_are_re_exported():
    """So nobody has to import from core to set one."""
    assert hugmunn.Effort.EXHAUSTIVE > hugmunn.Effort.QUICK
    assert hugmunn.Persistence.RELENTLESS > hugmunn.Persistence.LIGHT
    assert hugmunn.Autonomy.FULL > hugmunn.Autonomy.CONFIRM_ALL


def test_the_errors_share_a_base():
    """One except clause should be able to catch anything deliberate."""
    for error in (hugmunn.ModelNotFound, hugmunn.ApprovalRequired):
        assert issubclass(error, hugmunn.HugmunnError)


def test_the_version_is_importable_and_matches_the_package():
    """Catches a version bumped in one place and not the other.

    In an editable install the metadata is only rewritten by ``pip install
    -e .``, so this also fails when the package has been bumped and not
    reinstalled -- which is worth knowing before a release, and is exactly
    the state it caught on the way to 0.0.0.3.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("hugmunn")
    except PackageNotFoundError:
        pytest.skip("not installed; nothing to compare against")
    assert hugmunn.__version__ == installed, (
        f"package says {hugmunn.__version__}, installed metadata says "
        f"{installed} — run: pip install -e .")


# --------------------------------------------------------------- discovery


def test_models_lists_local_and_cloud():
    keys = {m.key for m in hugmunn.models()}
    assert "code-glm" in keys
    assert any(k.startswith("claude:") for k in keys)
    assert any(k.startswith("chatgpt:") for k in keys)


def test_models_can_be_filtered_by_provider():
    assert all(m.provider == "local" for m in hugmunn.models("local"))
    assert all(m.is_local is False for m in hugmunn.models("claude"))


def test_a_model_reports_what_a_caller_needs_to_choose():
    model = next(m for m in hugmunn.models("local") if m.key == "uncensored-gemma")
    assert model.freedom == "unlocked"
    assert model.context > 0 and model.size_gb > 0
    assert model.description


def test_available_models_is_a_subset_of_models():
    assert set(m.key for m in hugmunn.available_models()) <= set(
        m.key for m in hugmunn.models())


def test_skills_and_tools_are_listed_by_name():
    # recall is bound to one agent's result store, so it is created per
    # agent rather than living in the global registry.
    assert "read_file" in hugmunn.tool_names()
    assert "run_command" in hugmunn.tool_names()
    assert len(hugmunn.skills()) > 10


# ---------------------------------------------------------------- the Agent


def test_an_unknown_model_says_so_and_says_where_to_look():
    with pytest.raises(hugmunn.ModelNotFound) as caught:
        hugmunn.Agent("no-such-model")
    assert "hugmunn.models()" in str(caught.value)


def test_a_cloud_model_without_a_key_refuses_clearly(monkeypatch):
    from hugmunn import api

    monkeypatch.setattr(api._cli, "status", lambda *a, **kw: api._cli.Status(True, False, "Not connected"))
    with pytest.raises(hugmunn.HugmunnError) as caught:
        hugmunn.Agent("claude:default")
    assert "sign_in" in str(caught.value)


def test_a_local_model_will_not_start_a_server_when_told_not_to(monkeypatch):
    from hugmunn.core.client import LlamaClient

    monkeypatch.setattr(LlamaClient, "is_ready", lambda self: False)
    with pytest.raises(hugmunn.HugmunnError) as caught:
        hugmunn.Agent("code-glm", start_server=False)
    assert "not running" in str(caught.value)


class FakeClient:
    """Answers immediately, and records what it was asked to do."""

    def __init__(self):
        self.calls = []

    def is_ready(self):
        return True

    def stream(self, messages, tools=None, temperature=None, max_tokens=4096,
               cancel=None, thinking=None):
        from hugmunn.core.client import Event as CoreEvent

        self.calls.append({"messages": list(messages), "tools": tools,
                           "thinking": thinking})
        yield CoreEvent("content", text="an answer")
        yield CoreEvent("done", timings={"predicted_per_second": 40.0})


@pytest.fixture()
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(hugmunn.Agent, "_client", lambda self, start: client)
    return client


def test_ask_returns_the_answer_as_a_string(fake):
    with hugmunn.agent("code-glm") as a:
        assert a.ask("hello") == "an answer"


def test_run_yields_typed_events(fake):
    with hugmunn.agent("code-glm") as a:
        kinds = [e.kind for e in a.run("hello")]
    assert "content" in kinds and kinds[-1] == "done"


def test_the_conversation_accumulates_across_calls(fake):
    with hugmunn.agent("code-glm") as a:
        a.ask("first")
        a.ask("second")
        assert [m["role"] for m in a.history].count("user") == 2


def test_reset_clears_the_conversation_but_not_the_agent(fake):
    with hugmunn.agent("code-glm") as a:
        a.ask("hello")
        a.reset()
        assert a.history == []
        assert a.ask("again") == "an answer"


def test_tools_are_off_unless_asked_for(fake):
    """A library that runs shell commands the moment it is imported into
    somebody's script deserves the review it gets."""
    with hugmunn.agent("code-glm") as a:
        a.ask("hello")
    assert fake.calls[0]["tools"] is None


def test_tools_true_gives_the_built_in_set(fake):
    with hugmunn.agent("code-glm", tools=True) as a:
        a.ask("hello")
    assert fake.calls[0]["tools"]


def test_a_tool_list_narrows_to_those_tools(fake):
    with hugmunn.agent("code-glm", tools=["read_file"]) as a:
        a.ask("hello")
    names = {t["function"]["name"] for t in fake.calls[0]["tools"]}
    assert "read_file" in names
    assert "run_command" not in names


def test_without_approve_a_mutating_call_raises_rather_than_failing_quietly():
    """A silent refusal buried in a stream is a bug report waiting to happen."""
    from hugmunn.api import _deny

    with pytest.raises(hugmunn.ApprovalRequired) as caught:
        _deny("write_file", "config.py", {})
    assert "approve=" in str(caught.value)


def test_the_tiers_reach_the_underlying_agent(fake):
    with hugmunn.agent("code-glm", effort=hugmunn.Effort.EXHAUSTIVE,
                       persistence=hugmunn.Persistence.RELENTLESS,
                       autonomy=hugmunn.Autonomy.FULL) as a:
        assert a._agent.effort is hugmunn.Effort.EXHAUSTIVE
        assert a._agent.persistence is hugmunn.Persistence.RELENTLESS
        assert a._agent.autonomy is hugmunn.Autonomy.FULL


def test_tiers_accept_plain_integers(fake):
    with hugmunn.agent("code-glm", effort=4, persistence=4, autonomy=1) as a:
        assert a._agent.effort is hugmunn.Effort.EXHAUSTIVE


def test_skills_are_attached_by_name(fake):
    name = hugmunn.skills()[0]
    with hugmunn.agent("code-glm", skills=[name]) as a:
        assert [s.key for s in a._agent.active_skills] == [name]


def test_thinking_is_passed_through(fake):
    with hugmunn.agent("code-glm", thinking=False) as a:
        a.ask("hello")
    assert fake.calls[0]["thinking"] is False


def test_cancelling_ends_the_turn(fake):
    stop = threading.Event()
    stop.set()
    with hugmunn.agent("code-glm") as a:
        events = list(a.run("hello", cancel=stop))
    assert events and events[0].kind == "done"


def test_a_closed_agent_refuses_to_run(fake):
    a = hugmunn.Agent("code-glm")
    a.close()
    with pytest.raises(hugmunn.HugmunnError):
        list(a.run("hello"))


def test_close_is_idempotent(fake):
    a = hugmunn.Agent("code-glm")
    a.close()
    a.close()


def test_the_context_manager_closes_on_the_way_out(fake):
    with hugmunn.agent("code-glm") as a:
        pass
    assert a._closed


# ---------------------------------------------------------------- sessions


def test_a_conversation_round_trips_through_a_file(fake, tmp_path):
    path = str(tmp_path / "chat.json")
    with hugmunn.agent("code-glm") as a:
        a.ask("what is in config.py")
        a.save(path)

    with hugmunn.agent("code-glm") as b:
        assert b.load(path) == len(a.history)
        assert any("config.py" in str(m.get("content")) for m in b.history)


def test_loading_a_file_that_is_not_a_conversation_says_so(fake, tmp_path):
    bad = tmp_path / "nope.json"
    bad.write_text("{not json", encoding="utf-8")
    with hugmunn.agent("code-glm") as a:
        with pytest.raises(hugmunn.HugmunnError):
            a.load(str(bad))


def test_sessions_lists_saved_conversations(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    assert isinstance(hugmunn.sessions(), list)


# ------------------------------------------------------------ signing in


def test_sign_in_rejects_an_unknown_provider():
    with pytest.raises(hugmunn.ModelNotFound):
        hugmunn.sign_in("altavista", "key")


def test_signed_in_answers_for_both_spellings(monkeypatch):
    """CLI subscription status supports the documented provider aliases."""
    from hugmunn import api

    monkeypatch.setattr(api._cli, "status", lambda *a, **kw: api._cli.Status(True, True, "Connected"))
    assert hugmunn.signed_in("claude") and hugmunn.signed_in("anthropic")
    assert hugmunn.signed_in("chatgpt") and hugmunn.signed_in("openai")
