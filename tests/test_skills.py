"""Skill loading, grouping, and prompt composition."""

from __future__ import annotations

from localagent.core import skills


def write_skill(tmp_path, key, *, name="X", category="Core", default=False, body="Body."):
    path = tmp_path / f"{key}.md"
    path.write_text(
        f"---\nname: {name}\ncategory: {category}\n"
        f"description: d\ndefault: {'true' if default else 'false'}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class TestParsing:
    def test_reads_frontmatter_and_body(self, tmp_path):
        write_skill(tmp_path, "a", name="Alpha", category="Coding", body="Do the thing.")
        (skill,) = skills.load_all(tmp_path)
        assert skill.key == "a"
        assert skill.name == "Alpha"
        assert skill.category == "Coding"
        assert skill.body == "Do the thing."

    def test_default_flag_parsed(self, tmp_path):
        write_skill(tmp_path, "on", default=True)
        write_skill(tmp_path, "off", default=False)
        loaded = {s.key: s.default_on for s in skills.load_all(tmp_path)}
        assert loaded == {"on": True, "off": False}

    def test_file_without_frontmatter_skipped(self, tmp_path):
        (tmp_path / "bad.md").write_text("no frontmatter here", encoding="utf-8")
        write_skill(tmp_path, "good")
        assert [s.key for s in skills.load_all(tmp_path)] == ["good"]

    def test_file_without_name_skipped(self, tmp_path):
        (tmp_path / "bad.md").write_text("---\ncategory: Core\n---\n\nbody", encoding="utf-8")
        assert skills.load_all(tmp_path) == []

    def test_empty_body_skipped(self, tmp_path):
        (tmp_path / "bad.md").write_text("---\nname: X\n---\n\n   \n", encoding="utf-8")
        assert skills.load_all(tmp_path) == []

    def test_missing_directory_is_empty(self, tmp_path):
        assert skills.load_all(tmp_path / "nope") == []


class TestGrouping:
    def test_core_sorts_first(self, tmp_path):
        write_skill(tmp_path, "z", name="Z", category="Writing")
        write_skill(tmp_path, "a", name="A", category="Core")
        write_skill(tmp_path, "m", name="M", category="Coding")
        assert list(skills.by_category(skills.load_all(tmp_path)))[0] == "Core"

    def test_every_skill_lands_in_a_group(self, tmp_path):
        for i, cat in enumerate(["Core", "Coding", "Coding", "Web"]):
            write_skill(tmp_path, f"s{i}", name=f"S{i}", category=cat)
        loaded = skills.load_all(tmp_path)
        grouped = skills.by_category(loaded)
        assert sum(len(g) for g in grouped.values()) == len(loaded)

    def test_default_keys(self, tmp_path):
        write_skill(tmp_path, "on1", default=True)
        write_skill(tmp_path, "on2", default=True)
        write_skill(tmp_path, "off", default=False)
        assert skills.default_keys(skills.load_all(tmp_path)) == {"on1", "on2"}


class TestCompose:
    def test_no_skills_returns_prompt_unchanged(self):
        assert skills.compose("base prompt", []) == "base prompt"

    def test_skill_body_appended_and_fenced(self, tmp_path):
        write_skill(tmp_path, "a", name="Alpha", body="Always do X.")
        out = skills.compose("base", skills.load_all(tmp_path))
        assert out.startswith("base")
        assert 'name="Alpha"' in out
        assert "Always do X." in out

    def test_multiple_skills_all_present(self, tmp_path):
        write_skill(tmp_path, "a", name="A", body="AAA")
        write_skill(tmp_path, "b", name="B", body="BBB")
        out = skills.compose("base", skills.load_all(tmp_path))
        assert "AAA" in out and "BBB" in out


class TestCost:
    def test_tokens_scale_with_body(self, tmp_path):
        write_skill(tmp_path, "small", body="x" * 40)
        write_skill(tmp_path, "big", body="x" * 4000)
        loaded = {s.key: s.approx_tokens for s in skills.load_all(tmp_path)}
        assert loaded["big"] > loaded["small"] * 10

    def test_total_is_sum(self, tmp_path):
        write_skill(tmp_path, "a", body="x" * 400)
        write_skill(tmp_path, "b", body="x" * 800)
        loaded = skills.load_all(tmp_path)
        assert skills.total_tokens(loaded) == sum(s.approx_tokens for s in loaded)


class TestShippedSkills:
    """The skills that ship with the app must actually be loadable."""

    def test_all_load(self):
        assert len(skills.load_all()) >= 5

    def test_some_are_default_on(self):
        assert skills.default_keys(skills.load_all())

    def test_defaults_stay_cheap(self):
        """Defaults ride on every request — keep them small enough for a 16K window."""
        on = [s for s in skills.load_all() if s.default_on]
        assert skills.total_tokens(on) < 2000

    def test_keys_unique(self):
        keys = [s.key for s in skills.load_all()]
        assert len(keys) == len(set(keys))
