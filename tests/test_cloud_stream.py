"""Drives a real HTTP round-trip against a stub that speaks each API's SSE.

The message conversion is unit-tested next door. This covers the half that
conversion tests cannot reach: whether the *streaming* is decoded correctly.
Anthropic's tool arguments arrive as ``input_json_delta`` fragments that are
only valid JSON once the block closes, and a decoder that parses each fragment
looks fine against a one-word argument and fails on a real path.

A stub server rather than mocks, because what is being checked is the wire
format, and a mock of the wire format is a restatement of my assumptions.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hugmunn.core import cloud
from hugmunn.core.providers import CloudModel, Provider

CLAUDE = CloudModel("claude-opus-5", "Opus 5", Provider.ANTHROPIC,
                    thinking=True, max_output=16000)
GPT = CloudModel("gpt-5.1", "GPT-5.1", Provider.OPENAI, thinking=True)


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


ANTHROPIC_TEXT = [
    {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 10}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "thinking_delta", "thinking": "Let me check. "}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "text_delta", "text": "The answer "}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "text_delta", "text": "is 4."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
     "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
]

# Two tools in one turn, arguments split mid-token — the shape that breaks a
# decoder which parses each fragment instead of accumulating.
ANTHROPIC_TOOLS = [
    {"type": "message_start", "message": {"id": "msg_2", "usage": {}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "text_delta", "text": "Reading both."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_a", "name": "read_file"}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"pa'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": 'th": "/tmp/one.txt"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "toolu_b", "name": "read_file"}},
    {"type": "content_block_delta", "index": 2,
     "delta": {"type": "input_json_delta", "partial_json": '{"path": "/tmp/two.txt"}'}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
     "usage": {"output_tokens": 17}},
    {"type": "message_stop"},
]

OPENAI_TOOLS = [
    {"choices": [{"delta": {"content": "Reading."}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": '{"pa'}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": 'th": "/tmp/one.txt"}'}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    {"usage": {"completion_tokens": 9}},
]


class _Stub(BaseHTTPRequestHandler):
    script: list[dict] = []
    status: int = 200
    error_body: bytes = b""
    received: dict = {}

    def do_POST(self):  # noqa: N802 - http.server naming
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        type(self).received = {
            "path": self.path,
            "headers": dict(self.headers),
            "payload": json.loads(body or b"{}"),
        }
        if self.status != 200:
            self.send_response(self.status)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(self.error_body)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if self.path.endswith("/chat/completions"):
            payload = "".join(f"data: {json.dumps(e)}\n\n" for e in self.script)
            payload += "data: [DONE]\n\n"
            self.wfile.write(payload.encode())
        else:
            self.wfile.write(_sse(self.script))

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture()
def stub(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    monkeypatch.setattr(cloud, "ANTHROPIC_API", base)
    monkeypatch.setattr(cloud, "OPENAI_API", base)
    yield _Stub
    server.shutdown()
    server.server_close()


def collect(client, **kwargs):
    return list(client.stream([{"role": "user", "content": "hi"}], **kwargs))


# --------------------------------------------------------------- Anthropic


def test_anthropic_text_and_thinking_arrive_as_separate_event_kinds(stub):
    """Thinking must not land in the visible answer."""
    stub.script, stub.status = ANTHROPIC_TEXT, 200
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"))

    thinking = "".join(e.text for e in events if e.kind == "reasoning")
    content = "".join(e.text for e in events if e.kind == "content")
    assert thinking == "Let me check. "
    assert content == "The answer is 4."
    assert events[-1].kind == "done"


def test_anthropic_reports_a_generation_rate(stub):
    stub.script, stub.status = ANTHROPIC_TEXT, 200
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"))
    assert events[-1].timings["predicted_per_second"] > 0


def test_anthropic_tool_arguments_are_accumulated_across_fragments(stub):
    """The fragments are not individually valid JSON, and that is the point."""
    stub.script, stub.status = ANTHROPIC_TOOLS, 200
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"))

    calls = [c for e in events if e.kind == "tool_calls" for c in e.tool_calls]
    assert len(calls) == 2
    assert calls[0].name == "read_file"
    assert calls[0].parsed_arguments() == {"path": "/tmp/one.txt"}
    assert calls[1].parsed_arguments() == {"path": "/tmp/two.txt"}
    assert [c.id for c in calls] == ["toolu_a", "toolu_b"]


def test_anthropic_sends_the_documented_headers_and_endpoint(stub):
    stub.script, stub.status = ANTHROPIC_TEXT, 200
    collect(cloud.AnthropicClient(CLAUDE, "sk-ant-secret"))
    assert stub.received["path"].endswith("/messages")
    assert stub.received["headers"]["x-api-key"] == "sk-ant-secret"
    assert stub.received["headers"]["anthropic-version"] == cloud.ANTHROPIC_VERSION
    # Not Bearer: sending an Authorization header here is the classic
    # OpenAI-shim mistake and fails as a 401 with a confusing message.
    assert "authorization" not in {k.lower() for k in stub.received["headers"]}


def test_anthropic_tools_go_over_the_wire_as_input_schema(stub):
    stub.script, stub.status = ANTHROPIC_TEXT, 200
    collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"), tools=[{
        "type": "function",
        "function": {"name": "read_file", "description": "read",
                     "parameters": {"type": "object", "properties": {}}},
    }])
    tool = stub.received["payload"]["tools"][0]
    assert "input_schema" in tool and "parameters" not in tool


def test_an_http_error_becomes_a_readable_event_not_an_exception(stub):
    stub.script, stub.status = [], 401
    stub.error_body = json.dumps({"error": {"message": "invalid x-api-key"}}).encode()
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-wrong"))
    assert events[0].kind == "error"
    assert "sign in" in events[0].text.lower()


def test_a_mid_stream_error_event_is_surfaced(stub):
    stub.script, stub.status = [
        {"type": "message_start", "message": {}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ], 200
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"))
    assert events[-1].kind == "error" and "Overloaded" in events[-1].text


def test_cancelling_stops_the_stream(stub):
    stub.script, stub.status = ANTHROPIC_TEXT, 200
    cancel = threading.Event()
    cancel.set()
    events = collect(cloud.AnthropicClient(CLAUDE, "sk-ant-x"), cancel=cancel)
    assert events[0].kind == "done" and events[0].text == "cancelled"


# ------------------------------------------------------------------ OpenAI


def test_openai_tool_fragments_are_accumulated(stub):
    stub.script, stub.status = OPENAI_TOOLS, 200
    events = collect(cloud.OpenAIClient(GPT, "sk-x"))
    calls = [c for e in events if e.kind == "tool_calls" for c in e.tool_calls]
    assert len(calls) == 1
    assert calls[0].parsed_arguments() == {"path": "/tmp/one.txt"}


def test_openai_sends_a_bearer_token_and_reasoning_effort(stub):
    stub.script, stub.status = OPENAI_TOOLS, 200
    collect(cloud.OpenAIClient(GPT, "sk-secret", reasoning_effort="high"))
    assert stub.received["headers"]["Authorization"] == "Bearer sk-secret"
    assert stub.received["payload"]["reasoning_effort"] == "high"
    # A reasoning model rejects both `temperature` and the older token field.
    assert "temperature" not in stub.received["payload"]
    assert "max_completion_tokens" in stub.received["payload"]


# ------------------------------------------------------- the agent loop
#
# The point of the adapter is that everything above the client is unchanged.
# This runs the real agent loop, with a real tool, over the Anthropic wire
# format — the path a user actually takes.


def test_the_agent_loop_runs_a_tool_round_trip_over_anthropic(stub, tmp_path):
    from hugmunn.core.agent import Agent

    target = tmp_path / "one.txt"
    target.write_text("file contents here", encoding="utf-8")

    script = json.loads(json.dumps(ANTHROPIC_TOOLS))  # deep copy
    for event in script:
        if event.get("type") == "content_block_delta" and \
                event["delta"].get("type") == "input_json_delta":
            event["delta"]["partial_json"] = event["delta"]["partial_json"].replace(
                "/tmp/one.txt", str(target)).replace("/tmp/two.txt", str(target))

    stub.status = 200
    stub.script = script

    agent = Agent(
        client=cloud.AnthropicClient(CLAUDE, "sk-ant-x"),
        workdir=str(tmp_path),
        system_prompt="be terse",
        use_tools=True,
        auto_approve_reads=True,
    )

    history: list = [{"role": "user", "content": "read it"}]
    seen = []
    generated = agent.run(history, approve=lambda *a: True)
    for index, event in enumerate(generated):
        seen.append(event)
        # Swap in the final answer once the tools have been executed, the way
        # the API would on the follow-up request.
        if event.kind == "tool_result":
            stub.script = ANTHROPIC_TEXT

    kinds = [e.kind for e in seen]
    assert "tool_start" in kinds and "tool_result" in kinds
    assert kinds[-1] == "done"

    results = [e for e in seen if e.kind == "tool_result"]
    assert len(results) == 2
    assert "file contents here" in results[0].text

    # The history the next turn is built from must round-trip back through
    # convert_messages without losing the tool_use/tool_result pairing.
    system, converted = cloud.AnthropicClient.convert_messages(history)
    ids = [b["id"] for m in converted if isinstance(m["content"], list)
           for b in m["content"] if b.get("type") == "tool_use"]
    result_ids = [b["tool_use_id"] for m in converted if isinstance(m["content"], list)
                  for b in m["content"] if b.get("type") == "tool_result"]
    assert ids == result_ids, "every tool_use must have a matching tool_result"


@pytest.mark.parametrize("model_id,effort", [("gpt-6-astra", "low"), ("gpt-6-sol", "none"), ("gpt-6-luna", "none")])
def test_gpt6_responses_tools_and_reasoning_replay(stub, model_id, effort):
    thought = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
    call = {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path":"a"}'}
    stub.status = 200
    stub.script = [
        {"type": "response.output_text.delta", "delta": "Reading."},
        {"type": "response.output_item.done", "item": thought},
        {"type": "response.output_item.done", "item": call},
        {"type": "response.completed", "response": {"usage": {"output_tokens": 9}}},
    ]
    c = cloud.OpenAIClient(CloudModel(model_id, model_id, Provider.OPENAI, thinking=True), "test", reasoning_effort="minimal")
    events = collect(c, tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}])
    assert [e.kind for e in events] == ["content", "tool_calls", "done"]
    assert events[1].tool_calls[0].parsed_arguments() == {"path": "a"}
    assert stub.received["path"] == "/v1/responses"
    p = stub.received["payload"]
    assert p["reasoning"]["effort"] == effort and p["store"] is False
    assert p["tools"][0]["strict"] is False
    inputs = c._response_input([
        {"role": "assistant", "content": "Reading.", "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": call["arguments"]}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ])
    assert inputs[0] == thought and inputs[-1]["type"] == "function_call_output"
    c._response_input([{"role": "user", "content": "new conversation"}])
    assert not c._reasoning_items


@pytest.mark.parametrize("ending", ["response.incomplete", "response.failed", None])
def test_gpt6_does_not_execute_partial_calls(stub, ending):
    stub.status = 200
    stub.script = [{"type": "response.output_item.done", "item": {
        "type": "function_call", "call_id": "c1", "name": "write_file", "arguments": "{}"}}]
    if ending:
        stub.script.append({"type": ending})
    c = cloud.OpenAIClient(CloudModel("gpt-6-sol", "Sol", Provider.OPENAI), "test")
    events = collect(c)
    assert [e.kind for e in events] == ["error"]


def test_claude_adaptive_thinking_signature_survives_tool_round(stub):
    stub.status = 200
    stub.script = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "plan"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed"}},
        {"type": "content_block_stop", "index": 0},
        *ANTHROPIC_TOOLS[4:],
    ]
    c = cloud.AnthropicClient(CloudModel("claude-opus-5-5", "Opus", Provider.ANTHROPIC, thinking=True), "test")
    events = collect(c)
    p = stub.received["payload"]
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"] == {"effort": "low"}
    payload = c._payload([{"role": "assistant", "tool_calls": [{"id": "toolu_a", "function": {"name": "read_file", "arguments": "{}"}}]},
                          {"role": "tool", "tool_call_id": "toolu_a", "content": "x"}], None, 0.7, 4096)
    assert payload["messages"][0]["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "signed"}
    assert "temperature" not in payload
