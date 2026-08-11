"""The agent loop: stream a turn, run any tools the model asks for, repeat.

Kept free of Qt so it can be exercised from tests or a plain script. The UI
supplies two callbacks — one to approve mutating tool calls, one to observe
progress — and drives everything else through the yielded events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from . import autonomy as autonomykit
from . import effort as effortkit
from . import skills as skillkit
from . import subagent as subagentkit
from . import tools as toolkit
from .client import Event, LlamaClient, ToolCall


@dataclass
class AgentEvent:
    """What the UI renders. ``kind`` drives the widget chosen for it."""

    kind: str  # reasoning | content | tool_start | tool_result | denied | done | error
    text: str = ""
    tool_name: str = ""
    tool_summary: str = ""
    tool_id: str = ""
    timings: dict[str, Any] = field(default_factory=dict)


ApprovalFn = Callable[[str, str, dict[str, Any]], bool]
"""(tool_name, one_line_summary, arguments) -> allowed. Expected to block."""


class Agent:
    def __init__(
        self,
        client: LlamaClient,
        workdir: str,
        system_prompt: str,
        use_tools: bool = True,
        auto_approve_reads: bool = True,
        max_iterations: int = 12,
        active_skills: list[skillkit.Skill] | None = None,
        extra_tools: list[toolkit.Tool] | None = None,
        effort: effortkit.Effort | None = None,
        autonomy: autonomykit.Autonomy | None = None,
        tool_allowlist: set[str] | None = None,
        thinking: bool | None = None,
        depth: int = 0,
    ) -> None:
        self.thinking = thinking
        self.extra_tools = list(extra_tools or [])
        self.client = client
        self.workdir = workdir
        self.use_tools = use_tools
        self.auto_approve_reads = auto_approve_reads
        self.max_iterations = max_iterations
        self.active_skills = active_skills or []
        self.effort = effort or effortkit.Effort.STANDARD
        self.autonomy = autonomy or autonomykit.Autonomy.ASK_TO_WRITE
        # None means "every registered tool"; a subagent gets a narrowed set.
        self.tool_allowlist = tool_allowlist
        self.depth = depth

        # Subagents are a tier-4 capability, and only the top-level agent gets
        # them — a child that could spawn children fans out without bound.
        if effortkit.grants_subagents(self.effort) and depth == 0:
            self.extra_tools.append(
                toolkit.Tool(
                    name="spawn_agent",
                    description=subagentkit.DESCRIPTION,
                    parameters=subagentkit.SCHEMA,
                    run=subagentkit.make_spawn_agent(client, workdir, depth),
                )
            )

        # Compose once at construction: the skill set is fixed for a turn, and
        # rebuilding the prompt per iteration would churn the prompt cache.
        prompt = skillkit.compose(system_prompt, self.active_skills)
        self.system_prompt = f"{prompt}\n\n{effortkit.instructions(self.effort)}"

    def run(
        self,
        history: list[dict[str, Any]],
        approve: ApprovalFn,
        cancel: Any = None,
    ) -> Iterator[AgentEvent]:
        """Drive one user turn to completion, including any tool round-trips.

        ``history`` is mutated in place so the caller keeps the full transcript
        (assistant turns and tool results included) for the next turn.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        # User plugins are appended after the built-ins so a plugin cannot
        # shadow a core tool by reusing its name.
        by_name = {t.name: t for t in self.extra_tools}
        schemas = None
        if self.use_tools:
            builtin = [
                s for s in toolkit.schemas()
                if self.tool_allowlist is None
                or s["function"]["name"] in self.tool_allowlist
            ]
            schemas = builtin + [
                t.schema() for t in self.extra_tools if t.name not in toolkit.BY_NAME
            ]

        for iteration in range(self.max_iterations):
            if cancel is not None and cancel.is_set():
                yield AgentEvent("done", text="cancelled")
                return

            content_parts: list[str] = []
            calls: list[ToolCall] = []
            timings: dict[str, Any] = {}
            failed = False

            for event in self.client.stream(
                messages, tools=schemas, cancel=cancel, thinking=self.thinking
            ):
                if event.kind == "reasoning":
                    yield AgentEvent("reasoning", text=event.text)
                elif event.kind == "content":
                    content_parts.append(event.text)
                    yield AgentEvent("content", text=event.text)
                elif event.kind == "tool_calls":
                    calls.extend(event.tool_calls)
                elif event.kind == "error":
                    yield AgentEvent("error", text=event.text)
                    failed = True
                    break
                elif event.kind == "done" and event.timings:
                    timings = event.timings

            if failed:
                return

            answer = "".join(content_parts)

            if not calls:
                if answer:
                    history.append({"role": "assistant", "content": answer})
                yield AgentEvent("done", timings=timings)
                return

            # Record the assistant turn verbatim, tool calls included — the model
            # needs to see its own request alongside the result on the next pass.
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": answer or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in calls
                ],
            }
            history.append(assistant_turn)
            messages.append(assistant_turn)

            for call in calls:
                if cancel is not None and cancel.is_set():
                    yield AgentEvent("done", text="cancelled")
                    return

                args = call.parsed_arguments()
                summary = toolkit.summarize_call(call.name, args)
                spec = toolkit.BY_NAME.get(call.name) or by_name.get(call.name)
                yield AgentEvent(
                    "tool_start", tool_name=call.name, tool_summary=summary, tool_id=call.id
                )

                # The autonomy level is the authority on whether a human sees
                # this call; the tool's own flag only raises the floor.
                verdict = autonomykit.decide(
                    call.name, args, self.workdir, self.autonomy,
                    tool_requires_approval=bool(spec and spec.requires_approval),
                )
                needs_ok = verdict.needs_approval
                if needs_ok or not self.auto_approve_reads:
                    if not approve(call.name, summary, args):
                        denial = "User denied this tool call. Do not retry it; ask what to do instead."
                        yield AgentEvent(
                            "denied", tool_name=call.name, tool_summary=summary, text=denial
                        )
                        result_msg = {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": denial,
                        }
                        history.append(result_msg)
                        messages.append(result_msg)
                        continue

                output = toolkit.execute(
                    call.name, args, self.workdir, extra=by_name
                )
                yield AgentEvent(
                    "tool_result", tool_name=call.name, tool_summary=summary,
                    tool_id=call.id, text=output,
                )
                result_msg = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": output,
                }
                history.append(result_msg)
                messages.append(result_msg)

        yield AgentEvent(
            "error",
            text=(
                f"Stopped after {self.max_iterations} tool rounds without a final "
                "answer. Raise the limit in Settings, or ask something narrower."
            ),
        )
