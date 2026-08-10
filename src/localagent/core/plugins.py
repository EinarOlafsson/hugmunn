"""User-authored tools loaded from disk.

This is what makes "ask the model to write a tool" produce something that
actually runs. A plugin is a ``.py`` file in ``~/.config/localagent/tools/``
declaring four module-level names and a ``run`` function.

**Loading is opt-in and the reason is not paranoia.** The models already have
``run_command``, so arbitrary code execution is not a new capability here. What
changes is the review posture: ``run_command`` is approved per invocation and
the human sees the exact command, whereas a loaded plugin runs unreviewed on
every subsequent call. So a generated tool has to be enabled deliberately after
someone has read it, and plugins are read only from the user's config
directory — never from the session working directory, which the model can
write to freely.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tools import Tool

PLUGIN_DIR = Path.home() / ".config" / "localagent" / "tools"

REQUIRED = ("NAME", "DESCRIPTION", "PARAMETERS", "run")


@dataclass(frozen=True)
class PluginLoad:
    """Result of trying to load one file — success or a readable reason."""

    path: Path
    tool: Tool | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.tool is not None


def _load_one(path: Path) -> PluginLoad:
    spec = importlib.util.spec_from_file_location(f"localagent_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        return PluginLoad(path, None, "could not be imported")

    module = importlib.util.module_from_spec(spec)
    try:
        # Register before exec so a plugin that imports itself doesn't recurse.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken plugin must not kill startup
        sys.modules.pop(spec.name, None)
        return PluginLoad(path, None, f"{type(exc).__name__}: {exc}")

    missing = [n for n in REQUIRED if not hasattr(module, n)]
    if missing:
        return PluginLoad(path, None, f"missing {', '.join(missing)}")
    if not callable(module.run):
        return PluginLoad(path, None, "run is not callable")
    if not isinstance(module.PARAMETERS, dict):
        return PluginLoad(path, None, "PARAMETERS must be a JSON-schema dict")

    return PluginLoad(
        path,
        Tool(
            name=str(module.NAME),
            description=str(module.DESCRIPTION),
            parameters=module.PARAMETERS,
            run=module.run,
            requires_approval=bool(getattr(module, "REQUIRES_APPROVAL", False)),
        ),
    )


def discover(directory: Path | None = None) -> list[PluginLoad]:
    """Load every plugin, reporting failures rather than raising them."""
    root = directory or PLUGIN_DIR
    if not root.is_dir():
        return []
    return [_load_one(p) for p in sorted(root.glob("*.py")) if not p.name.startswith("_")]


def loaded_tools(results: list[PluginLoad], enabled: set[str] | None = None) -> list[Tool]:
    """The subset the user has switched on. ``None`` means none — opt-in."""
    active = enabled or set()
    return [r.tool for r in results if r.ok and r.tool.name in active]


TEMPLATE = '''\
"""One-line summary of what this tool does."""

NAME = "tool_name"                      # snake_case; must be unique
DESCRIPTION = (
    "What it does and WHEN to call it. Be prescriptive about the trigger "
    "condition — that is what drives whether the model reaches for it."
)
PARAMETERS = {                          # JSON Schema for the arguments
    "type": "object",
    "properties": {
        "example": {"type": "string", "description": "What this argument is."},
    },
    "required": ["example"],
}
REQUIRES_APPROVAL = False               # True for anything destructive or outbound


def run(workdir: str, example: str) -> str:
    """Return a string. Raise nothing — return an error message instead.

    workdir is the session working directory. Confine any file access to it.
    """
    return f"got {example!r}"
'''
