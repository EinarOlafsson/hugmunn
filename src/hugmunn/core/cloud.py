"""Streaming clients for Anthropic and OpenAI, yielding the same events as the local one.

The agent loop calls ``client.stream(messages, tools=…, cancel=…)`` and reads
:class:`~hugmunn.core.client.Event`. Anything that satisfies that works, so
adding a provider is an adapter rather than a second agent.

OpenAI is nearly free: its chat-completions API *is* the shape llama.cpp
imitates, so :class:`OpenAIClient` is the local client plus an auth header and
a reasoning parameter.

Anthropic is a genuinely different API and the translation is where the care
goes:

===================  ==============================  ===============================
                     OpenAI / llama.cpp              Anthropic
===================  ==============================  ===============================
endpoint             ``/v1/chat/completions``        ``/v1/messages``
auth                 ``Authorization: Bearer``       ``x-api-key`` + version header
system prompt        a message with role ``system``  a top-level ``system`` field
tool schema          ``function.parameters``         ``input_schema``
model asks for tool  ``delta.tool_calls[]``          a ``tool_use`` content block
tool result          one message, role ``tool``      a ``tool_result`` block in *user*
max tokens           optional                        required
thinking             ``reasoning_effort``            ``thinking.budget_tokens``
===================  ==============================  ===============================

The last two rows are the ones that bite. Anthropic has no ``tool`` role at
all — results go back as content blocks inside a user message — and *every
result for one assistant turn must be in a single user message*. The OpenAI
format puts each in its own message, so consecutive tool results are merged
here. Sending them separately is accepted for a single call and fails the
moment a model requests two tools at once, which is exactly the case that is
easy to miss in testing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from .client import Event, ToolCall
from .providers import ANTHROPIC_API, ANTHROPIC_VERSION, OPENAI_API, CloudModel, Provider


def _explain(provider: Provider, status: int, body: str) -> str:
    """Turn an API error into something the user can act on."""
    name = "Anthropic" if provider == Provider.ANTHROPIC else "OpenAI"
    detail = body[:300]
    try:
        parsed = json.loads(body)
        detail = (parsed.get("error") or {}).get("message") or detail
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass

    if status in (401, 403):
        return (
            f"{name} rejected the API key.\n\nOpen Settings → Accounts and sign "
            f"in again. If the key was revoked or rotated, paste the new one.\n\n"
            f"{name} said: {detail}"
        )
    if status == 404:
        return (
            f"{name} does not have that model on this account.\n\nThe model list "
            f"refreshes when you sign in — reopen Settings → Accounts and press "
            f"Refresh.\n\n{name} said: {detail}"
        )
    if status == 429:
        return (
            f"{name} is rate-limiting this key.\n\nEither too many requests in a "
            f"short window, or the account is out of credit. Wait and retry, or "
            f"check the billing page.\n\n{name} said: {detail}"
        )
    if status == 400 and "credit" in detail.lower():
        return f"{name}: the account has no credit left.\n\n{detail}"
    if status == 400:
        return (
            f"{name} rejected the request.\n\nA prompt longer than the model's "
            f"context is the usual cause — disable some skills in the sidebar.\n\n"
            f"{name} said: {detail}"
        )
    if status >= 500:
        return f"{name} had a server error ({status}). This is their side; retry.\n\n{detail}"
    return f"{name} returned HTTP {status}: {detail}"


class _CloudBase:
    """Shared plumbing. A cloud model has no server to start."""

    provider: Provider = Provider.LOCAL

    def __init__(self, model: CloudModel, api_key: str, timeout: float = 600.0) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def is_ready(self) -> bool:
        """A key is the whole readiness condition — there is no process to wait on."""
        return bool(self.api_key)

    def model_name(self) -> str:
        return self.model.id

    @staticmethod
    def _rate(output_tokens: int, started: float) -> dict[str, Any]:
        """Report tok/s in the same shape llama.cpp does, so the UI is unchanged.

        Measured wall-clock rather than reported by the API, because neither
        provider reports one. It therefore includes network latency, which is
        the honest number for "how fast did that feel".
        """
        elapsed = max(time.monotonic() - started, 1e-6)
        return {"predicted_per_second": output_tokens / elapsed,
                "predicted_n": output_tokens}


# ------------------------------------------------------------------ OpenAI


class OpenAIClient(_CloudBase):
    """Chat Completions. Close enough to llama.cpp that only auth differs."""

    provider = Provider.OPENAI

    def __init__(self, model: CloudModel, api_key: str, timeout: float = 600.0,
                 reasoning_effort: str | None = None) -> None:
        super().__init__(model, api_key, timeout)
        self.reasoning_effort = reasoning_effort

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        cancel: Any = None,
        thinking: bool | None = None,   # set by the effort tier, not per call
    ) -> Iterator[Event]:
        payload: dict[str, Any] = {
            "model": self.model.id,
            "messages": messages,
            "stream": True,
            # Reasoning models count thinking against the output budget and
            # reject the older parameter name outright.
            "max_completion_tokens": max(max_tokens, self.model.max_output),
            "stream_options": {"include_usage": True},
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        }
        if self.model.thinking and self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        elif temperature is not None:
            # A reasoning model rejects temperature; a plain one accepts it.
            payload["temperature"] = temperature

        pending: dict[int, dict[str, str]] = {}
        started = time.monotonic()
        produced = 0
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=15.0)) as client:
                with client.stream(
                    "POST", f"{OPENAI_API}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")
                        yield Event("error", text=_explain(self.provider, response.status_code, body))
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
                        if usage := chunk.get("usage"):
                            produced = usage.get("completion_tokens", produced)
                        for event in self._consume(chunk, pending):
                            yield event
        except httpx.HTTPError as exc:
            yield Event("error", text=f"Could not reach OpenAI: {exc}")
            return

        if pending:
            yield Event("tool_calls", tool_calls=_finalize(pending))
        yield Event("done", timings=self._rate(produced, started))

    @staticmethod
    def _consume(chunk: dict[str, Any], pending: dict[int, dict[str, str]]) -> Iterator[Event]:
        choices = chunk.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}
        # Some gateways surface a reasoning summary under either name.
        if summary := (delta.get("reasoning_content") or delta.get("reasoning")):
            if isinstance(summary, str):
                yield Event("reasoning", text=summary)
        if content := delta.get("content"):
            yield Event("content", text=content)
        for call in delta.get("tool_calls") or []:
            slot = pending.setdefault(call.get("index", 0), {"id": "", "name": "", "arguments": ""})
            if call_id := call.get("id"):
                slot["id"] = call_id
            fn = call.get("function") or {}
            if name := fn.get("name"):
                slot["name"] = name
            if args := fn.get("arguments"):
                slot["arguments"] += args
        if choices[0].get("finish_reason") == "tool_calls" and pending:
            yield Event("tool_calls", tool_calls=_finalize(pending))
            pending.clear()


# --------------------------------------------------------------- Anthropic


class AnthropicClient(_CloudBase):
    """The Messages API. A real adapter, not a shim — see the module docstring."""

    provider = Provider.ANTHROPIC

    def __init__(self, model: CloudModel, api_key: str, timeout: float = 600.0,
                 thinking_budget: int = 0) -> None:
        super().__init__(model, api_key, timeout)
        self.thinking_budget = thinking_budget

    # ---- request translation ----

    @staticmethod
    def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """OpenAI ``function`` wrappers to Anthropic's flat tool objects."""
        out = []
        for tool in tools or []:
            fn = tool.get("function") or tool
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Split the system prompt out and rewrite the rest as content blocks.

        Returns ``(system_text, messages)``. Two things this has to get right
        that a naive per-message map does not:

        * A ``tool`` message becomes a ``tool_result`` block in a **user**
          message — Anthropic has no tool role.
        * Consecutive tool results are merged into one user message. The API
          requires every result for a given assistant turn to arrive together,
          so emitting one message each works until a model calls two tools in
          one turn and then fails with a mismatched-tool_use_id error.
        """
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content")

            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue

            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id", ""),
                    "content": str(content or ""),
                }
                # Append to the open user message when the previous entry was
                # also a result, rather than starting a new one.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][-1].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": arguments if isinstance(arguments, dict) else {},
                    })
                # An assistant turn with neither text nor a call is not
                # representable and the API rejects an empty content array.
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
                continue

            if content:
                out.append({"role": "user", "content": str(content)})

        return "\n\n".join(system_parts), out

    #: Output tokens kept clear of the thinking budget so there is room to
    #: answer. Thinking is spent from the same ``max_tokens`` allowance, so a
    #: budget equal to the ceiling ends the turn mid-thought and shows the
    #: user nothing at all.
    ANSWER_ROOM = 4096

    #: Anthropic's own minimum. A smaller budget is rejected, so it is
    #: dropped rather than sent.
    MIN_BUDGET = 1024

    def _payload(self, messages, tools, temperature, max_tokens) -> dict[str, Any]:
        system, converted = self.convert_messages(messages)
        # Required here, unlike OpenAI. The model's own ceiling is the cap:
        # asking for more than it can emit is a 400.
        ceiling = max(max_tokens, self.model.max_output)
        payload: dict[str, Any] = {
            "model": self.model.id,
            "messages": converted,
            "stream": True,
            "max_tokens": ceiling,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self.convert_tools(tools)
        if self.model.thinking and self.thinking_budget:
            # Carve the budget out of the ceiling rather than adding to it.
            # An effort tier asking for more thinking than the model can emit
            # in total is clamped down, not allowed to push max_tokens past
            # what the model accepts.
            budget = min(self.thinking_budget, ceiling - self.ANSWER_ROOM)
            if budget >= self.MIN_BUDGET:
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
                return payload  # thinking forbids temperature
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    # ---- streaming ----

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        cancel: Any = None,
        thinking: bool | None = None,   # set by the effort tier, not per call
    ) -> Iterator[Event]:
        payload = self._payload(messages, tools, temperature, max_tokens)
        # Tool arguments arrive as partial JSON on input_json_delta and are
        # only parseable once the block closes, so they are accumulated by
        # block index exactly as OpenAI's fragments are.
        blocks: dict[int, dict[str, str]] = {}
        calls: list[ToolCall] = []
        produced = 0
        started = time.monotonic()

        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=15.0)) as client:
                with client.stream(
                    "POST", f"{ANTHROPIC_API}/messages",
                    json=payload,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")
                        yield Event("error", text=_explain(self.provider, response.status_code, body))
                        return

                    for line in response.iter_lines():
                        if cancel is not None and cancel.is_set():
                            yield Event("done", text="cancelled")
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            chunk = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue

                        kind = chunk.get("type")
                        if kind == "content_block_start":
                            block = chunk.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                blocks[chunk.get("index", 0)] = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "arguments": "",
                                }
                        elif kind == "content_block_delta":
                            delta = chunk.get("delta") or {}
                            dtype = delta.get("type")
                            if dtype == "text_delta":
                                yield Event("content", text=delta.get("text", ""))
                            elif dtype == "thinking_delta":
                                yield Event("reasoning", text=delta.get("thinking", ""))
                            elif dtype == "input_json_delta":
                                slot = blocks.get(chunk.get("index", 0))
                                if slot is not None:
                                    slot["arguments"] += delta.get("partial_json", "")
                        elif kind == "content_block_stop":
                            slot = blocks.pop(chunk.get("index", 0), None)
                            if slot and slot["name"]:
                                calls.append(ToolCall(
                                    id=slot["id"] or f"call_{len(calls)}",
                                    name=slot["name"],
                                    arguments=slot["arguments"] or "{}",
                                ))
                        elif kind == "message_delta":
                            produced = (chunk.get("usage") or {}).get("output_tokens", produced)
                        elif kind == "error":
                            detail = (chunk.get("error") or {}).get("message", "unknown error")
                            yield Event("error", text=f"Anthropic: {detail}")
                            return
        except httpx.HTTPError as exc:
            yield Event("error", text=f"Could not reach Anthropic: {exc}")
            return

        if calls:
            yield Event("tool_calls", tool_calls=calls)
        yield Event("done", timings=self._rate(produced, started))


def _finalize(pending: dict[int, dict[str, str]]) -> list[ToolCall]:
    return [
        ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=slot["arguments"])
        for idx, slot in sorted(pending.items())
        if slot["name"]
    ]


def build(model: CloudModel, api_key: str, effort_level: int = 2) -> _CloudBase:
    """The right client for a model, with the effort tier already mapped."""
    from .effort import Effort, anthropic_budget, openai_effort

    level = Effort(effort_level)
    if model.provider == Provider.ANTHROPIC:
        return AnthropicClient(model, api_key, thinking_budget=anthropic_budget(level))
    return OpenAIClient(model, api_key, reasoning_effort=openai_effort(level))
