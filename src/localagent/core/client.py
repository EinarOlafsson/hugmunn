"""Streaming client for llama.cpp's OpenAI-compatible endpoint.

Yields typed events rather than raw chunks so the UI never parses SSE itself.
Two llama.cpp specifics are handled here:

* ``reasoning_content`` — a separate delta field produced when the server runs
  with ``--reasoning-format deepseek``. These models think by default, so
  without splitting it out the chain of thought lands in the visible answer.
* ``timings`` — llama.cpp attaches tok/s to the final chunk; OpenAI does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text; may be malformed, parse defensively

    def parsed_arguments(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


@dataclass
class Event:
    kind: str  # "reasoning" | "content" | "tool_calls" | "done" | "error"
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    timings: dict[str, Any] = field(default_factory=dict)


class LlamaClient:
    """Thin streaming wrapper. One instance per base URL."""

    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---------- health ----------

    def is_ready(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=2.0)
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        try:
            # llama-server reports {"status": "loading model"} while warming up.
            return r.json().get("status", "ok") == "ok"
        except (json.JSONDecodeError, ValueError):
            return True

    def model_name(self) -> str:
        try:
            r = httpx.get(f"{self.base_url}/v1/models", timeout=5.0)
            data = r.json().get("data") or []
            return data[0].get("id", "unknown") if data else "unknown"
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, IndexError, AttributeError):
            return "unknown"

    # ---------- streaming ----------

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        cancel: Any = None,  # threading.Event-like; .is_set() ends the stream
    ) -> Iterator[Event]:
        payload: dict[str, Any] = {
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            # llama-server already has per-model sampling from its launch flags;
            # only override when the caller explicitly asks.
            **({"temperature": temperature} if temperature is not None else {}),
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        }

        pending: dict[int, dict[str, str]] = {}
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
                with client.stream(
                    "POST", f"{self.base_url}/v1/chat/completions", json=payload
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")[:500]
                        yield Event("error", text=f"HTTP {response.status_code}: {body}")
                        return

                    for line in response.iter_lines():
                        if cancel is not None and cancel.is_set():
                            yield Event("done", text="cancelled")
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        for event in self._consume_chunk(chunk, pending):
                            yield event

        except httpx.HTTPError as exc:
            yield Event("error", text=f"connection failed: {exc}")
            return

        if pending:
            yield Event("tool_calls", tool_calls=self._finalize(pending))
        yield Event("done")

    @staticmethod
    def _consume_chunk(chunk: dict[str, Any], pending: dict[int, dict[str, str]]) -> Iterator[Event]:
        choices = chunk.get("choices") or []
        if not choices:
            return
        choice = choices[0]
        delta = choice.get("delta") or {}

        if reasoning := delta.get("reasoning_content"):
            yield Event("reasoning", text=reasoning)
        if content := delta.get("content"):
            yield Event("content", text=content)

        # Tool calls stream in fragments keyed by index; arguments arrive as
        # partial JSON text that must be concatenated before parsing.
        for call in delta.get("tool_calls") or []:
            idx = call.get("index", 0)
            slot = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if call_id := call.get("id"):
                slot["id"] = call_id
            fn = call.get("function") or {}
            if name := fn.get("name"):
                slot["name"] = name
            if args := fn.get("arguments"):
                slot["arguments"] += args

        if choice.get("finish_reason") == "tool_calls" and pending:
            yield Event("tool_calls", tool_calls=LlamaClient._finalize(pending))
            pending.clear()

        if timings := chunk.get("timings"):
            yield Event("done", timings=timings)

    @staticmethod
    def _finalize(pending: dict[int, dict[str, str]]) -> list[ToolCall]:
        return [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=slot["arguments"],
            )
            for idx, slot in sorted(pending.items())
            if slot["name"]
        ]
