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
from . import persistence as persistkit
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


#: Sent when the round budget runs out. Tools are withheld on this pass, so
#: the only move left is to answer.
_FINAL_PASS = """
You have used the tool budget for this turn. No more tool calls are available.

Answer now from what you already have. Say what you established, what you did
not, and what the next step would be. A partial answer with its limits stated
is useful; another plan for what you would do is not.
""".strip()


#: Sentinel: an unset max_iterations means "ask the persistence tier". None
#: would be ambiguous with "no limit", which is not a thing this offers.
_UNSET = -1

#: Retained for the tests and callers that reference a default interval; the
#: live value comes from the persistence tier.
RECANVAS_EVERY = 10

#: The step back. Deliberately forbids attempting a fix during this round --
#: a model told to "consider gathering more information" will gather one fact
#: and immediately try again, which is the behaviour being interrupted.
_RECANVAS = """
Stop. You have spent {used} rounds attempting this and it has not landed.

Do not try another fix this round. Re-canvas first:

1. State what you now KNOW, having checked it — not what you inferred.
2. State what you ASSUMED without checking. One of these is usually wrong,
   and it is usually the one that felt too obvious to verify.
3. Name what you have NOT looked at: a file you inferred the contents of, a
   command whose output you predicted, a wider search you did not run.

Then go and look at that. Read the actual file. Run the command and read what
it really said. Search more broadly than feels necessary.

Only after that, say in one line what you are trying next and why this route
differs from the ones that failed.
""".strip()

#: Immediate, and the signal a model is least able to see in itself.
_REPEATING = """
That is the same call you just made:

  {call}

It will return the same thing. Change something real -- different tool,
different arguments, a different assumption -- or say what is blocking you.
""".strip()

#: After a failure, before the count justifies a full re-canvas.
_ADJUST = """
That did not work. Change one thing and try again rather than repeating it:
the arguments, the tool, or the assumption underneath. If two variations have
now failed, the assumption is the thing to change.
""".strip()

#: Near the ceiling. Converge rather than explore.
_CONVERGE = """
You are {used} of {total} rounds in. Stop exploring. Either answer now from
what you have, or make the single call that would settle it and then answer.
""".strip()


class Agent:
    def __init__(
        self,
        client: LlamaClient,
        workdir: str,
        system_prompt: str,
        use_tools: bool = True,
        auto_approve_reads: bool = True,   # deprecated; autonomy decides
        max_iterations: int = _UNSET,
        active_skills: list[skillkit.Skill] | None = None,
        extra_tools: list[toolkit.Tool] | None = None,
        effort: effortkit.Effort | None = None,
        persistence: persistkit.Persistence | None = None,
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
        # Retained so old callers still construct. It no longer gates
        # anything: Autonomy.CONFIRM_ALL is what "confirm reads too" means.
        self.auto_approve_reads = auto_approve_reads
        self.max_iterations = max_iterations
        self.active_skills = active_skills or []
        self.effort = effort or effortkit.Effort.STANDARD
        self.persistence = persistence or persistkit.Persistence.NORMAL
        # Persistence owns the round budget. An explicit max_iterations still
        # wins so a subagent can be given a tighter one, but nothing in the UI
        # sets it: two controls over one decision is the bug that made the
        # autonomy tier look broken.
        if max_iterations == _UNSET:
            max_iterations = persistkit.max_rounds(self.persistence)
        self.max_iterations = max_iterations
        self.recanvas_every = persistkit.recanvas_every(self.persistence)
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
        self.system_prompt = (
            f"{prompt}\n\n{effortkit.instructions(self.effort)}"
            f"\n\n{persistkit.instructions(self.persistence)}"
        )

    def _nudge(self, iteration: int, attempted: list[tuple[str, str]],
               failures: int = 0) -> str:
        """What to say to a run that is not landing, and when.

        Four phases, in the order they become true:

        * an exact repeat, which is always worth interrupting immediately;
        * a failure, met with "change one thing" rather than encouragement;
        * every :data:`RECANVAS_EVERY` rounds, a forced step back that
          forbids attempting a fix and requires gathering instead;
        * near the ceiling, converge.

        Nothing is said to a run that is going fine. Advice injected into a
        working loop is noise the model has to spend tokens dismissing.
        """
        if not attempted:
            return ""

        if len(attempted) >= 2 and attempted[-1] == attempted[-2]:
            return _REPEATING.format(call=f"{attempted[-1][0]}  {attempted[-1][1]}")

        # The step back, on the cycle rather than once. A long run should be
        # made to re-canvas repeatedly: the second one is often where the
        # wrong assumption from the first is finally noticed.
        if iteration and iteration % self.recanvas_every == 0:
            if iteration < self.max_iterations - 2:
                return _RECANVAS.format(used=iteration)

        if iteration == max(1, int(self.max_iterations * 0.85)):
            return _CONVERGE.format(used=iteration, total=self.max_iterations)

        if failures >= 1:
            return _ADJUST
        return ""

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

        # What has already been tried, so a loop can be named as a loop.
        attempted: list[tuple[str, str]] = []
        # Tool output that looks like a failure. Not exact -- tools report
        # errors as text -- but a wrong "that did not work" costs one line
        # and a missed one costs a wasted round.
        recent_failures = 0

        for iteration in range(self.max_iterations):
            nudge = self._nudge(iteration, attempted, recent_failures)
            if nudge:
                # Injected as a tool-style observation rather than a system
                # message: a second system turn is not representable on
                # Anthropic, and the model treats a mid-conversation user note
                # as something to act on rather than as configuration.
                note = {"role": "user", "content": nudge}
                messages.append(note)
                yield AgentEvent("notice", text=nudge)

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
                attempted.append((call.name, summary))
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
                # The autonomy level is the only authority. This used to
                # read `if needs_ok or not self.auto_approve_reads`, which
                # let a checkbox predating autonomy override every tier --
                # so "4 · Full" still confirmed every call and the setting
                # looked broken.
                if verdict.needs_approval:
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
                lowered = output[:400].lower()
                if any(marker in lowered for marker in (
                        "error", "failed", "not found", "no such file",
                        "traceback", "permission denied", "cannot")):
                    recent_failures += 1
                else:
                    recent_failures = 0
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

        # The budget is spent. Erroring here throws away everything the run
        # actually learned, which is the worst possible outcome: the tools ran,
        # the results are in the history, and the user gets a message about
        # round counts. So make one more pass with the tools removed -- the
        # model has no option but to answer from what it already has.
        yield AgentEvent("notice", text=(
            f"Reached {self.max_iterations} tool rounds. Writing up what was "
            f"found so far."))
        messages.append({"role": "user", "content": _FINAL_PASS})

        content_parts = []
        for event in self.client.stream(messages, tools=None, cancel=cancel,
                                        thinking=self.thinking):
            if event.kind == "content":
                content_parts.append(event.text)
                yield AgentEvent("content", text=event.text)
            elif event.kind == "reasoning":
                yield AgentEvent("reasoning", text=event.text)
            elif event.kind == "error":
                yield AgentEvent("error", text=event.text)
                return

        answer = "".join(content_parts)
        if answer:
            history.append({"role": "assistant", "content": answer})
            yield AgentEvent("done")
            return

        yield AgentEvent("error", text=(
            f"Stopped after {self.max_iterations} tool rounds and could not "
            f"summarise what was found. Raise the limit in Settings, or ask "
            f"something narrower."
        ))
