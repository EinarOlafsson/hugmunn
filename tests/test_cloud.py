"""The Anthropic adapter, where the format translation actually happens."""

from __future__ import annotations

import json

import pytest

from hugmunn.core import effort as effortkit
from hugmunn.core.cloud import AnthropicClient, OpenAIClient, _explain
from hugmunn.core.providers import CloudModel, Provider

MODEL = CloudModel("claude-opus-5", "Opus 5", Provider.ANTHROPIC, thinking=True,
                   max_output=16000)
GPT = CloudModel("gpt-5.1", "GPT-5.1", Provider.OPENAI, thinking=True)


def client(**kwargs) -> AnthropicClient:
    return AnthropicClient(MODEL, "sk-ant-test", **kwargs)


# ------------------------------------------------------- message conversion


def test_system_prompt_moves_out_of_the_message_list():
    system, messages = AnthropicClient.convert_messages([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ])
    assert system == "be terse"
    assert messages == [{"role": "user", "content": "hello"}]


def test_several_system_messages_are_joined_rather_than_dropped():
    system, _ = AnthropicClient.convert_messages([
        {"role": "system", "content": "one"},
        {"role": "system", "content": "two"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "one\n\ntwo"


def test_assistant_tool_calls_become_tool_use_blocks():
    _, messages = AnthropicClient.convert_messages([
        {"role": "user", "content": "read it"},
        {
            "role": "assistant",
            "content": "Looking now.",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
            }],
        },
    ])
    blocks = messages[1]["content"]
    assert blocks[0] == {"type": "text", "text": "Looking now."}
    assert blocks[1] == {
        "type": "tool_use", "id": "call_1", "name": "read_file",
        "input": {"path": "a.txt"},
    }


def test_a_tool_result_becomes_a_user_message_not_a_tool_role():
    """Anthropic has no tool role at all; results ride inside a user turn."""
    _, messages = AnthropicClient.convert_messages([
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "f", "content": "output"},
    ])
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "output"}
    ]


def test_two_results_for_one_turn_are_merged_into_one_user_message():
    """The failure this prevents only appears when a model calls two tools.

    Emitting one user message per result is accepted for a single call and
    rejected the moment there are two, with a mismatched-tool_use_id error.
    """
    _, messages = AnthropicClient.convert_messages([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": "{}"}},
            {"id": "c2", "function": {"name": "g", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "first"},
        {"role": "tool", "tool_call_id": "c2", "content": "second"},
    ])
    results = [m for m in messages if m["role"] == "user"]
    assert len(results) == 1
    assert [b["tool_use_id"] for b in results[0]["content"]] == ["c1", "c2"]


def test_results_from_different_turns_are_not_merged():
    _, messages = AnthropicClient.convert_messages([
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "first"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "function": {"name": "g", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "second"},
    ])
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant", "user"]


def test_malformed_tool_arguments_do_not_raise():
    """A truncated stream leaves unparseable JSON; an empty input beats a crash."""
    _, messages = AnthropicClient.convert_messages([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": '{"path": "unclos'}},
        ]},
    ])
    assert messages[0]["content"][0]["input"] == {}


def test_an_empty_assistant_turn_is_dropped():
    """The API rejects a content array with nothing in it."""
    _, messages = AnthropicClient.convert_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None},
    ])
    assert len(messages) == 1


# ---------------------------------------------------------- tool conversion


def test_tools_are_flattened_and_the_schema_key_is_renamed():
    converted = AnthropicClient.convert_tools([{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }])
    assert converted == [{
        "name": "read_file",
        "description": "Read a file",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }]


def test_a_tool_without_parameters_still_gets_a_valid_schema():
    converted = AnthropicClient.convert_tools([{"function": {"name": "now"}}])
    assert converted[0]["input_schema"] == {"type": "object", "properties": {}}


# ----------------------------------------------------------------- payload


def test_thinking_leaves_room_to_answer():
    """The budget comes out of max_tokens, not on top of it.

    Spending the whole allowance on thinking ends the turn mid-thought and
    shows the user nothing, which reads as a hang rather than as a limit.
    """
    payload = client(thinking_budget=32_768)._payload(
        [{"role": "user", "content": "hi"}], None, None, 4096
    )
    budget = payload["thinking"]["budget_tokens"]
    assert payload["max_tokens"] - budget >= AnthropicClient.ANSWER_ROOM


def test_a_budget_larger_than_the_model_allows_is_clamped_not_sent():
    """Tier 4 asks for 32K; this model can emit 16K in total."""
    payload = client(thinking_budget=32_768)._payload(
        [{"role": "user", "content": "hi"}], None, None, 4096
    )
    assert payload["thinking"]["budget_tokens"] < 32_768
    assert payload["max_tokens"] <= MODEL.max_output


def test_a_budget_the_model_can_afford_is_sent_intact():
    big = CloudModel("claude-opus-5", "Opus 5", Provider.ANTHROPIC,
                     thinking=True, max_output=64_000)
    payload = AnthropicClient(big, "k", thinking_budget=32_768)._payload(
        [{"role": "user", "content": "hi"}], None, None, 4096
    )
    assert payload["thinking"]["budget_tokens"] == 32_768


def test_thinking_forbids_temperature():
    payload = client(thinking_budget=8192)._payload(
        [{"role": "user", "content": "hi"}], None, 0.7, 4096
    )
    assert "temperature" not in payload


def test_no_budget_means_no_thinking_block():
    payload = client(thinking_budget=0)._payload(
        [{"role": "user", "content": "hi"}], None, None, 4096
    )
    assert "thinking" not in payload


def test_a_budget_below_the_api_floor_is_dropped_rather_than_sent():
    payload = client(thinking_budget=200)._payload(
        [{"role": "user", "content": "hi"}], None, None, 4096
    )
    assert "thinking" not in payload


def test_max_tokens_is_always_present():
    """Optional on OpenAI, required here — omitting it is a 400."""
    payload = client()._payload([{"role": "user", "content": "hi"}], None, None, 100)
    assert payload["max_tokens"] >= 100


# ------------------------------------------------------------ effort tiers


@pytest.mark.parametrize("level", list(effortkit.Effort))
def test_every_effort_tier_maps_to_both_providers(level):
    assert isinstance(effortkit.anthropic_budget(level), int)
    assert effortkit.openai_effort(level) in ("minimal", "low", "medium", "high")


def test_effort_budgets_increase_with_the_tier():
    budgets = [effortkit.anthropic_budget(level) for level in effortkit.Effort]
    assert budgets == sorted(budgets)
    assert budgets[0] == 0        # tier 1 turns thinking off


def test_openai_client_sends_reasoning_effort_not_temperature():
    """A reasoning model rejects temperature outright."""
    from hugmunn.core import cloud

    built = cloud.build(GPT, "sk-test", effort_level=3)
    assert isinstance(built, OpenAIClient)
    assert built.reasoning_effort == "medium"


def test_build_picks_the_client_from_the_model_provider():
    from hugmunn.core import cloud

    assert isinstance(cloud.build(MODEL, "k", 2), AnthropicClient)
    assert isinstance(cloud.build(GPT, "k", 2), OpenAIClient)


# ------------------------------------------------------------ error mapping


def test_an_auth_failure_names_the_fix_rather_than_the_status_code():
    message = _explain(Provider.ANTHROPIC, 401, json.dumps(
        {"error": {"message": "invalid x-api-key"}}))
    assert "Settings" in message and "sign in" in message.lower()


def test_a_rate_limit_distinguishes_throttling_from_no_credit():
    assert "credit" in _explain(Provider.OPENAI, 429, "{}").lower()


def test_an_unparseable_error_body_still_produces_a_message():
    assert "500" in _explain(Provider.OPENAI, 500, "<html>bad gateway</html>")
