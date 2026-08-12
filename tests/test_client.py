"""Streaming parser tests.

The fiddly part is tool calls: llama.cpp streams them as fragments keyed by
index, with the JSON arguments split across arbitrary chunk boundaries.
"""

from __future__ import annotations

from hugmunn.core.client import LlamaClient, ToolCall


def consume(chunks):
    """Replay chunks through the parser and collect the events."""
    pending: dict[int, dict[str, str]] = {}
    events = []
    for chunk in chunks:
        events.extend(LlamaClient._consume_chunk(chunk, pending))
    return events, pending


def delta(**fields):
    return {"choices": [{"delta": fields, "index": 0}]}


class TestTextStreaming:
    def test_content_deltas(self):
        events, _ = consume([delta(content="Hel"), delta(content="lo")])
        assert [e.text for e in events if e.kind == "content"] == ["Hel", "lo"]

    def test_reasoning_kept_separate_from_content(self):
        events, _ = consume([delta(reasoning_content="hmm"), delta(content="answer")])
        kinds = [(e.kind, e.text) for e in events]
        assert ("reasoning", "hmm") in kinds
        assert ("content", "answer") in kinds

    def test_empty_choices_ignored(self):
        events, _ = consume([{"choices": []}, {}])
        assert events == []

    def test_timings_surface_on_done(self):
        events, _ = consume([{"choices": [{"delta": {}}], "timings": {"predicted_per_second": 37.4}}])
        done = [e for e in events if e.kind == "done"]
        assert done and done[0].timings["predicted_per_second"] == 37.4


class TestToolCallAccumulation:
    def test_arguments_reassembled_across_chunks(self):
        events, pending = consume([
            delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"pa'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "a.py"}'}}]),
        ])
        assert not [e for e in events if e.kind == "tool_calls"]  # not finished yet
        calls = LlamaClient._finalize(pending)
        assert calls[0].name == "read_file"
        assert calls[0].parsed_arguments() == {"path": "a.py"}

    def test_finish_reason_emits_calls(self):
        events, _ = consume([
            delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "list_directory", "arguments": "{}"}}]),
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])
        emitted = [e for e in events if e.kind == "tool_calls"]
        assert emitted and emitted[0].tool_calls[0].name == "list_directory"

    def test_parallel_calls_kept_distinct(self):
        _, pending = consume([
            delta(tool_calls=[
                {"index": 0, "id": "a", "function": {"name": "read_file", "arguments": '{"path":"x"}'}},
                {"index": 1, "id": "b", "function": {"name": "list_directory", "arguments": "{}"}},
            ]),
        ])
        calls = LlamaClient._finalize(pending)
        assert [c.name for c in calls] == ["read_file", "list_directory"]

    def test_nameless_fragment_dropped(self):
        _, pending = consume([delta(tool_calls=[{"index": 0, "function": {"arguments": "{}"}}])])
        assert LlamaClient._finalize(pending) == []


class TestToolCallParsing:
    def test_malformed_json_yields_empty_dict(self):
        assert ToolCall("i", "n", "{not json").parsed_arguments() == {}

    def test_empty_arguments_yield_empty_dict(self):
        assert ToolCall("i", "n", "").parsed_arguments() == {}

    def test_non_object_json_yields_empty_dict(self):
        assert ToolCall("i", "n", "[1,2]").parsed_arguments() == {}
