"""Effort tiers and the subagent grant."""

from __future__ import annotations

import pytest

from hugmunn.core import effort as effortkit
from hugmunn.core import subagent
from hugmunn.core.effort import Effort
from hugmunn.core.tools import ToolError


class TestTiers:
    def test_all_four_have_label_blurb_and_body(self):
        for level in Effort:
            assert effortkit.LABELS[level]
            assert effortkit.BLURBS[level]
            assert effortkit.BODIES[level].strip()

    def test_instructions_are_fenced_and_named(self):
        text = effortkit.instructions(Effort.THOROUGH)
        assert text.startswith("<effort level=")
        assert "Thorough" in text

    def test_higher_tiers_say_more(self):
        sizes = [len(effortkit.BODIES[l]) for l in Effort]
        assert sizes == sorted(sizes), sizes

    def test_only_the_top_tier_grants_subagents(self):
        granted = [l for l in Effort if effortkit.grants_subagents(l)]
        assert granted == [Effort.EXHAUSTIVE]


class TestSubagentGrant:
    def _spawn(self, depth=0):
        return subagent.make_spawn_agent(client=None, workdir="/tmp", depth=depth)

    def test_mutating_tools_cannot_be_delegated(self):
        for tool in ("write_file", "edit_file", "run_command", "python_exec", "download_pdfs"):
            with pytest.raises(ToolError, match="only.*read-only|cannot grant"):
                self._spawn()("/tmp", task="check something", tools=tool)

    def test_unknown_tool_refused(self):
        with pytest.raises(ToolError, match="cannot grant"):
            self._spawn()("/tmp", task="check", tools="rm_minus_rf")

    def test_recursion_blocked(self):
        with pytest.raises(ToolError, match="cannot spawn subagents"):
            self._spawn(depth=1)("/tmp", task="check", tools="read_file")

    def test_empty_task_refused(self):
        with pytest.raises(ToolError, match="must describe"):
            self._spawn()("/tmp", task="   ")

    def test_oversized_brief_refused(self):
        with pytest.raises(ToolError, match="keep briefs under"):
            self._spawn()("/tmp", task="x" * (subagent.MAX_TASK_CHARS + 1))

    def test_grantable_set_is_read_only(self):
        from hugmunn.core import tools
        for name in subagent.GRANTABLE:
            assert not tools.BY_NAME[name].requires_approval, name

    def test_schema_is_wellformed(self):
        assert subagent.SCHEMA["type"] == "object"
        assert "task" in subagent.SCHEMA["required"]


class TestAgentWiring:
    def test_effort_text_reaches_the_system_prompt(self):
        from hugmunn.core.agent import Agent
        a = Agent(client=None, workdir="/tmp", system_prompt="base", effort=Effort.THOROUGH)
        assert "Verify rather than assume" in a.system_prompt

    def test_spawn_agent_appears_only_at_tier_four(self):
        from hugmunn.core.agent import Agent
        for level in Effort:
            a = Agent(client=None, workdir="/tmp", system_prompt="b", effort=level)
            names = {t.name for t in a.extra_tools}
            assert ("spawn_agent" in names) == (level is Effort.EXHAUSTIVE), level

    def test_subagent_depth_never_gets_the_tool(self):
        from hugmunn.core.agent import Agent
        a = Agent(client=None, workdir="/tmp", system_prompt="b",
                  effort=Effort.EXHAUSTIVE, depth=1)
        assert "spawn_agent" not in {t.name for t in a.extra_tools}
