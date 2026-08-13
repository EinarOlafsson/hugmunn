"""hugmunn — a desktop client and Python library for local and cloud LLMs.

Named for Huginn and Muninn, Odin's ravens: *hugr*, thought, and *munr*,
memory. They fly out at dawn and return at dusk to report what the world is
doing, which is close enough.

The public interface is :mod:`hugmunn.api`, re-exported here::

    import hugmunn

    with hugmunn.agent("code-glm") as a:
        print(a.ask("what does this repository do?"))

Everything under ``hugmunn.core`` and ``hugmunn.ui`` is implementation and
changes without notice. If you find yourself importing from either, say so in
an issue -- it means the public surface is missing something.
"""

__version__ = "0.0.0.3"

from .api import (  # noqa: F401,E402
    Agent,
    ApprovalRequired,
    Autonomy,
    Effort,
    Event,
    HugmunnError,
    Model,
    ModelNotFound,
    Persistence,
    ServerError,
    Session,
    agent,
    available_models,
    models,
    sessions,
    sign_in,
    signed_in,
    skills,
    tool_names,
)

__all__ = [
    "Agent", "agent", "Event", "Model", "Session", "sessions",
    "Effort", "Persistence", "Autonomy",
    "models", "available_models", "skills", "tool_names",
    "sign_in", "signed_in",
    "HugmunnError", "ModelNotFound", "ApprovalRequired", "ServerError",
    "__version__",
]
