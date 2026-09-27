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
import hashlib
import os
import shutil
import tempfile
import zipfile
from functools import lru_cache
from html import escape

import yaml
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
    source_path: str = ""

    @property
    def approx_tokens(self) -> int:
        return max(1, len(self.rendered) // CHARS_PER_TOKEN)

    @property
    def rendered(self) -> str:
        """Body as it goes into the prompt, with the trigger line if present."""
        if self.source_path:
            return f"{self.description[:220]}\nRead instructions: {self.source_path}"
        if not self.when:
            return self.body
        return f"Apply this when: {self.when}\n\n{self.body}"


def _parse(path: Path) -> Skill | None:
    """Read one ``.md`` skill file. Returns None if it is malformed."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    match = _FRONTMATTER.match(raw)
    if match is None:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("name"), str):
        return None
    directory_skill = path.name == "SKILL.md"
    body = raw[match.end():].strip()
    if not body or "name" not in meta:
        return None

    return Skill(
        key=("codex-" + re.sub(r"[^a-z0-9-]+", "-", meta["name"].lower()).strip("-")) if directory_skill else path.stem,
        name=meta.get("name", path.stem),
        category=str(meta.get("category", "Codex" if directory_skill else "Other")),
        description=str(meta.get("description", "")),
        body=body,
        default_on=not directory_skill and str(meta.get("default", "")).lower() in ("true", "yes", "1"),
        when=str(meta.get("when", meta.get("description", "") if directory_skill else "")),
        source_path=str(path.resolve()) if directory_skill else "",
    )


def load_all(directory: Path | None = None) -> list[Skill]:
    """Load shipped skills plus anything in the user directory.

    A malformed file is skipped rather than raised — one bad skill should not
    stop the application from starting. Passing ``directory`` loads only that
    one, which is what the tests use.
    """
    if directory is None:
        try:
            install_bundled()
        except OSError:
            pass  # Read-only profiles can still use the built-in instruction packs.
    roots = [directory] if directory is not None else [SKILLS_DIR, user_directory()]
    by_key: dict[str, Skill] = {}
    for root in roots:
        if root is None or not root.is_dir():
            continue
        for path in sorted(set(root.glob("*.md")) | set(p for p in root.rglob("SKILL.md") if "bundled-codex" not in p.parts or _current_bundle(p))):
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
    if any(s.source_path for s in skills):
        parts.append("Codex skill entries are an index. Read the selected SKILL.md with read_file "
                     "only when relevant. Resolve relative resources and Codex installation paths "
                     "against that skill directory. Skills do not install connectors or grant "
                     "permission; use only tools available in this session.")
    for skill in skills:
        parts.append(f"<skill name=\"{escape(skill.name, quote=True)}\">\n{skill.rendered.strip()}\n</skill>")
    return "\n\n".join(parts)


def total_tokens(skills: list[Skill]) -> int:
    return sum(s.approx_tokens for s in skills)


def user_directory() -> Path:
    """Use the same profile as settings and sessions, including portable installs."""
    from ..config import config_dir
    # Preserve the historical constant as an override for integrations/tests.
    legacy = Path.home() / ".config" / "hugmunn" / "skills"
    return USER_SKILLS_DIR if USER_SKILLS_DIR != legacy else config_dir() / "skills"


def install_bundled() -> int:
    """Extract the versioned, licensed catalogue once. Never execute its scripts."""
    archive = SKILLS_DIR / "codex-library.zip"
    if not archive.is_file():
        return 0
    digest = _archive_digest(str(archive), archive.stat().st_mtime_ns)
    destination = user_directory() / "bundled-codex" / digest[:16]
    marker = destination / ".version"
    if marker.is_file() and marker.read_text() == digest:
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                path = (stage / info.filename).resolve()
                if not path.is_relative_to(stage.resolve()) or info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("Invalid path in bundled skill archive.")
            bundle.extractall(stage)
        (stage / ".version").write_text(digest)
        try:
            stage.replace(destination)
        except OSError:
            if not marker.is_file() or marker.read_text() != digest:
                raise
    return len(list(destination.rglob("SKILL.md")))


def import_directory(root: Path, destination: Path | None = None) -> int:
    """Copy skill bundles with support files, skipping symlinks and duplicates."""
    root = root.expanduser().resolve()
    destination = destination or user_directory() / "imported-codex"
    manifests = [root / "SKILL.md"] if (root / "SKILL.md").is_file() else sorted(root.rglob("SKILL.md"))
    count = 0
    for manifest in manifests:
        if manifest.is_symlink() or not manifest.resolve().is_relative_to(root):
            continue
        if destination.resolve() in manifest.resolve().parents:
            continue
        if _parse(manifest) is None:
            continue
        identity = hashlib.sha256(str(manifest.parent).encode()).hexdigest()[:10]
        target = destination / f"{manifest.parent.name}-{identity}"
        if (target / "SKILL.md").is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        def ignore(folder, names):
            return [n for n in names if n in (".git", "__pycache__", "node_modules", ".venv")
                    or (Path(folder) / n).is_symlink()]
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            stage = Path(temp) / "bundle"
            shutil.copytree(manifest.parent, stage, ignore=ignore)
            stage.replace(target)
        count += 1
    return count


def import_codex() -> int:
    """Save locally installed user/system/plugin skills in Hugmunn's profile."""
    codex = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    roots = [codex / "skills", Path.home() / ".agents" / "skills"]
    cache = codex / "plugins" / "cache"
    if cache.is_dir():
        # Use only the newest cached version of each plugin.
        for market in sorted(cache.iterdir()):
            if not market.is_dir():
                continue
            for plugin in sorted(market.iterdir()):
                if not plugin.is_dir():
                    continue
                versions = sorted((p for p in plugin.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
                if versions and (versions[-1] / "skills").is_dir():
                    roots.append(versions[-1] / "skills")
    return sum(import_directory(root) for root in roots if root.is_dir())


def _current_bundle(path: Path) -> bool:
    archive = SKILLS_DIR / "codex-library.zip"
    if not archive.is_file():
        return False
    return _archive_digest(str(archive), archive.stat().st_mtime_ns)[:16] in path.parts


@lru_cache(maxsize=4)
def _archive_digest(path: str, mtime_ns: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
