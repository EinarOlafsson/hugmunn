"""Desktop application and Python API for local and cloud language models.

Use :func:`agent` to manage a conversation and its local server::

    import hugmunn

    with hugmunn.agent("code-glm") as chat:
        print(chat.ask("Explain Python context managers."))

The selected local model needs downloaded weights and a llama-server runtime.
Cloud models use Claude Code and Codex subscription logins. Importing the package does not load Qt,
start a server, or contact a provider. Names in ``__all__`` form the public API;
``hugmunn.core`` and ``hugmunn.ui`` are implementation modules.
"""

from ._version import __version__

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
    ProviderError,
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
    "HugmunnError", "ModelNotFound", "ApprovalRequired", "ServerError", "ProviderError",
    "__version__",
]
