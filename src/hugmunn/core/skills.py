"""Skills: markdown instruction packs appended to the system prompt.

A skill is *only* instructions. It cannot give a model a new capability — that
takes a tool (see ``tools.py`` and ``web.py``). What a skill does is tell a
model that already has the capability how to use it well, or supply domain
conventions it would otherwise have to be told every session.

**Skills are not free, and this is the whole design constraint.** Every enabled
skill is prepended to the system prompt on every request, so it costs context
on every turn. These models run 16-64K windows; loading everything would leave
no room for the conversation. So skills are opt-in, grouped by category, and
each one reports its own cost. The ``core`` category is small and on by default;
everything else is off until asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
# Skills the user (or a model, via write_file) authors go here rather than into
# the installed package, so they survive reinstalls and need no write access to
# the repo. A user skill with the same filename as a shipped one wins.
USER_SKILLS_DIR = Path.home() / ".config" / "hugmunn" / "skills"

# Rough but honest: ~4 characters per token for English prose. Used only to
# show a cost figure in the UI, never for anything load-bearing.
CHARS_PER_TOKEN = 4

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    key: str
    name: str
    category: str
    description: str
    body: str
    default_on: bool = False
    # An explicit trigger condition, prepended to the body as "Apply this
    # when: ...". Without one, a skill that is switched on applies to every
    # turn indiscriminately — a model handed microscopy conventions while
    # writing a shell script is being pulled two ways. Stating when a skill
    # is relevant lets several be enabled at once without diluting each other,
    # which is what makes breadth and precision compatible rather than opposed.
    when: str = ""

    @property
    def approx_tokens(self) -> int:
        return max(1, len(self.rendered) // CHARS_PER_TOKEN)

    @property
    def rendered(self) -> str:
        """Body as it goes into the prompt, with the trigger line if present."""
        if not self.when:
            return self.body
        return f"Apply this when: {self.when}\n\n{self.body}"


def _parse(path: Path) -> Skill | None:
    """Read one ``.md`` skill file. Returns None if it is malformed."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _FRONTMATTER.match(raw)
    if match is None:
        return None

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()

    body = raw[match.end():].strip()
    if not body or "name" not in meta:
        return None

    return Skill(
        key=path.stem,
        name=meta.get("name", path.stem),
        category=meta.get("category", "Other"),
        description=meta.get("description", ""),
        body=body,
        default_on=meta.get("default", "").lower() in ("true", "yes", "1"),
        when=meta.get("when", ""),
    )


def load_all(directory: Path | None = None) -> list[Skill]:
    """Load shipped skills plus anything in the user directory.

    A malformed file is skipped rather than raised — one bad skill should not
    stop the application from starting. Passing ``directory`` loads only that
    one, which is what the tests use.
    """
    roots = [directory] if directory is not None else [SKILLS_DIR, USER_SKILLS_DIR]
    by_key: dict[str, Skill] = {}
    for root in roots:
        if root is None or not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            skill = _parse(path)
            if skill is not None:
                by_key[skill.key] = skill  # later root wins, so user overrides shipped
    return sorted(by_key.values(), key=lambda s: (s.category.lower(), s.name.lower()))


def by_category(skills: list[Skill]) -> dict[str, list[Skill]]:
    """Group for the UI. ``core`` sorts first; the rest alphabetically."""
    groups: dict[str, list[Skill]] = {}
    for skill in skills:
        groups.setdefault(skill.category, []).append(skill)
    ordered = sorted(groups, key=lambda c: (c.lower() != "core", c.lower()))
    return {c: groups[c] for c in ordered}


def default_keys(skills: list[Skill]) -> set[str]:
    return {s.key for s in skills if s.default_on}


def compose(system_prompt: str, skills: list[Skill]) -> str:
    """Build the final system prompt from the base plus enabled skills.

    Each skill is fenced with its name so the model can tell them apart and so
    a human reading a transcript can see exactly what was in play.
    """
    if not skills:
        return system_prompt
    parts = [system_prompt.strip()]
    for skill in skills:
        parts.append(f"<skill name=\"{skill.name}\">\n{skill.rendered.strip()}\n</skill>")
    return "\n\n".join(parts)


def total_tokens(skills: list[Skill]) -> int:
    return sum(s.approx_tokens for s in skills)
