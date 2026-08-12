"""``spawn_agent`` — a nested agent with a deliberately narrow grant.

Available only at effort tier 4. The parent states a task, names the tools the
child may use, and gets back the child's final answer as a string.

Three constraints, each closing a specific failure mode:

* **No recursion.** A child cannot spawn a child. Without a depth limit an
  agent that decides delegation is useful will fan out until something runs
  out — and on a local model each level costs a full generation pass.
* **Tools must be named explicitly, and default to none.** The parent has to
  decide what the child needs. A verification agent that can write files can
  "fix" the thing it was asked to check.
* **Mutating tools are refused outright.** ``write_file``, ``edit_file``,
  ``run_command``, ``python_exec`` and ``download_pdfs`` cannot be granted,
  because the human approving the parent's plan never saw the child's. If a
  subtask needs to change something, the parent does it after the child
  reports.
"""

from __future__ import annotations

from .tools import ToolError, _clip

MAX_ITERATIONS = 6
MAX_TASK_CHARS = 4_000

# A child may only ever read. See module docstring.
GRANTABLE = frozenset({
    "read_file", "list_directory", "search_text", "glob_files", "diff_files",
    "web_search", "web_fetch", "image_search", "sql_schema", "sql_query",
    "pubmed_search", "arxiv_search", "read_pdf", "image_info",
})

SUBAGENT_PROMPT = """\
You are a subagent answering one specific question for another agent.

Answer only what was asked. Do not expand the scope, do not suggest follow-up
work, and do not comment on the task itself.

Reason from what you can verify with your tools. You have not seen the parent's
reasoning and should not try to guess it — an independent answer is the entire
reason you were asked.

If the question cannot be answered with the tools you have, say exactly that
and say what you would have needed. A confident guess is worse than a reported
gap, because the parent cannot tell them apart.

End with your answer and your confidence in it. Be concise; your reply is read
by a program, not a person.
"""


def make_spawn_agent(client, workdir: str, depth: int = 0):
    """Build the ``spawn_agent`` callable bound to this session's client.

    Returned as a closure rather than a plain function because a subagent needs
    the same server connection and working directory as its parent, and neither
    can be passed through the tool-call arguments safely.
    """

    def spawn_agent(_workdir: str, task: str, tools: str = "", max_iterations: int = 4) -> str:
        if depth >= 1:
            raise ToolError(
                "subagents cannot spawn subagents. Do this work yourself, or "
                "ask the parent for a differently-scoped task."
            )
        if not task.strip():
            raise ToolError("`task` must describe what the subagent should determine")
        if len(task) > MAX_TASK_CHARS:
            raise ToolError(f"task is {len(task)} chars; keep briefs under {MAX_TASK_CHARS}")

        requested = [t.strip() for t in tools.split(",") if t.strip()]
        refused = [t for t in requested if t not in GRANTABLE]
        if refused:
            raise ToolError(
                f"cannot grant {', '.join(refused)} to a subagent — only "
                "read-only tools may be delegated. Do any writing or execution "
                "yourself after the subagent reports."
            )

        # Imported here: agent imports tools, tools would import this module.
        from .agent import Agent
        from . import tools as toolkit

        granted = [toolkit.by_name()[t] for t in requested]
        child = Agent(
            client=client,
            workdir=workdir,
            system_prompt=SUBAGENT_PROMPT,
            use_tools=bool(granted),
            auto_approve_reads=True,       # read-only by construction
            max_iterations=min(int(max_iterations), MAX_ITERATIONS),
            active_skills=[],
            extra_tools=[],
            tool_allowlist={t.name for t in granted},
            depth=depth + 1,
        )

        answer, used, errors = [], [], []
        for event in child.run([{"role": "user", "content": task}], approve=lambda *a: True):
            if event.kind == "content":
                answer.append(event.text)
            elif event.kind == "tool_start":
                used.append(event.tool_name)
            elif event.kind == "error":
                errors.append(event.text)

        body = "".join(answer).strip() or "(the subagent produced no answer)"
        header = f"subagent used {used or 'no tools'}"
        if errors:
            header += f"; errors: {errors[0][:120]}"
        return _clip(f"[{header}]\n\n{body}")

    return spawn_agent


SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "One specific question for the subagent, with the context it "
                "needs to answer. Do not tell it what you expect — an agent "
                "given your conclusion will tend to confirm it."
            ),
        },
        "tools": {
            "type": "string",
            "description": (
                "Comma-separated read-only tools to grant, e.g. "
                "'read_file,search_text'. Empty means none. Writing and "
                "execution tools cannot be granted."
            ),
        },
        "max_iterations": {
            "type": "integer",
            "description": "Tool rounds the subagent may take. Default 4, cap 6.",
        },
    },
    "required": ["task"],
}

DESCRIPTION = (
    "Run a fresh agent on one narrow question and return its answer. Use it to "
    "verify a conclusion independently — the subagent does not see your "
    "reasoning, which is what makes its agreement worth something — or to do a "
    "self-contained subtask whose intermediate detail you do not need. Grant "
    "only the read-only tools the question requires. Each call costs a full "
    "generation pass, so do not use it for work you could finish in a couple "
    "of tool calls."
)
