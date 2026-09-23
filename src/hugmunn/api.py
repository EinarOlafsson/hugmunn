"""Public model discovery, conversation, and credential interfaces.

Import these names from :mod:`hugmunn`. Local agents connect to llama.cpp;
cloud agents connect to the selected provider. Tools are disabled by default.
When enabled, the autonomy policy determines which calls require ``approve``.
Use an agent as a context manager to stop any server it starts.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence
from pathlib import Path

from .errors import HugmunnError
from .core.providers import ProviderError

from . import config as _config
from .core import agent as _agent
from .core import autonomy as _autonomy
from .core import credentials as _credentials
from .core import effort as _effort
from .core import persistence as _persistence
from .core import providers as _providers
from .core import sessions as _sessions
from .core import skills as _skills
from .core import tools as _tools
from .core.server import ServerError, ServerManager

__all__ = [
    "Agent", "agent", "Event", "Model", "Session", "sessions",
    "Effort", "Persistence", "Autonomy",
    "models", "available_models", "sign_in", "signed_in",
    "skills", "tool_names",
    "ApprovalRequired", "ModelNotFound", "ServerError", "ProviderError", "HugmunnError",
    "__version__",
]

from ._version import __version__  # noqa: E402  (re-exported deliberately)

#: Tiers. Re-exported so callers never import from ``hugmunn.core``.
Effort = _effort.Effort
Persistence = _persistence.Persistence
Autonomy = _autonomy.Autonomy


class ModelNotFound(HugmunnError):
    """The named model is not in the registry, or its provider is unknown."""


class ApprovalRequired(HugmunnError):
    """A tool requires approval and no callback was supplied.

    A callback returning ``False`` instead produces a ``denied`` event and
    lets the model continue without executing the tool.
    """


@dataclass(frozen=True)
class Event:
    """One thing that happened during a turn.

    ``kind`` is the field to switch on:

    ``reasoning``
        The model thinking, when the model separates it out. Not the answer.
    ``content``
        A fragment of the answer. Concatenate these in order.
    ``tool_start`` / ``tool_result``
        A tool was called, and what it returned.
    ``denied``
        The approval callback declined a tool call. The tool was not executed.
    ``notice``
        Something the run wants the user to know -- a strategy change, a
        context trim. Informational.
    ``error``
        The turn failed. ``text`` says why, in words meant for a person.
    ``done``
        The turn finished. ``timings`` carries tokens per second when the
        server reported it.
    """

    kind: str
    text: str = ""
    tool: str = ""
    summary: str = ""
    id: str = ""
    timings: dict[str, Any] | None = None


@dataclass(frozen=True)
class Model:
    """A model in the registry; availability depends on weights and credentials.

    ``key`` is what :class:`Agent` takes. For local models it is a short name
    such as ``"uncensored-gemma"``; for cloud models it is
    ``"claude:<id>"`` or ``"chatgpt:<id>"``.

    Attributes:
        key: Identifier accepted by :class:`Agent`.
        label: Display name.
        provider: ``"local"``, ``"claude"``, or ``"chatgpt"``.
        freedom: Registry classification: vanilla, tuned, or unlocked.
        context: Configured context capacity in tokens.
        size_gb: Estimated weight download size; zero for cloud models.
        downloaded: Whether weights exist locally, or a cloud key is present.
        description: Short model description from the registry or provider.
    """

    key: str
    label: str
    provider: str
    freedom: str = "vanilla"
    context: int = 0
    size_gb: float = 0.0
    downloaded: bool = False
    description: str = ""

    @property
    def is_local(self) -> bool:
        """Whether this model uses a local llama.cpp server."""
        return self.provider == "local"


def _wrap_local(spec) -> Model:
    return Model(
        key=spec.key, label=spec.label, provider="local",
        freedom=getattr(spec, "freedom", "vanilla"),
        context=spec.ctx_size, size_gb=spec.download_gb,
        downloaded=spec.has_weights(), description=spec.blurb,
    )


def _wrap_cloud(model) -> Model:
    provider = "claude" if model.provider is _providers.Provider.ANTHROPIC else "chatgpt"
    return Model(
        key=f"{provider}:{model.id}", label=model.label, provider=provider,
        freedom="vanilla", context=model.context, description=model.blurb,
        downloaded=_credentials.is_signed_in(model.provider),
    )


def models(provider: str | None = None) -> list[Model]:
    """Return registered models without making network requests.

    Args:
        provider: Filter by ``"local"``, ``"claude"``, or ``"chatgpt"``;
            ``None`` includes all providers. Unknown values return an empty list.

    Returns:
        Model metadata, including download status and context capacity.
        Cloud entries use the cached catalogue or the built-in fallback.
        Call :func:`sign_in` to refresh an account's catalogue.
    """
    # Restore paths chosen in the desktop before inspecting local availability.
    _config.Settings.load()
    out = [_wrap_local(s) for s in _config.REGISTRY]
    for cloud in _providers.CLOUD:
        out.extend(_wrap_cloud(m) for m in _providers.models_for(cloud))
    return [m for m in out if provider is None or m.provider == provider]


def available_models() -> list[Model]:
    """Return local models with weights and a launch path, plus signed-in cloud models.

    This checks local files and credentials, not memory capacity, server health,
    network connectivity, or API quota.
    """
    return [m for m in models()
            if (m.is_local and _config.by_key(m.key)
                and _config.by_key(m.key).is_available()) or (not m.is_local and m.downloaded)]


def skills() -> list[str]:
    """Names of the instruction packs that can be attached to an agent."""
    return [s.key for s in _skills.load_all()]


def tool_names() -> list[str]:
    """Return sorted built-in tool names for the ``Agent(tools=...)`` allowlist.

    Per-agent tools such as ``recall`` are not included.
    """
    return sorted(_tools.by_name())


def sign_in(provider: str, api_key: str) -> int:
    """Validate and store an API key, then cache the provider's model catalogue.

    Args:
        provider: ``"claude"``/``"anthropic"`` or ``"chatgpt"``/``"openai"``.
        api_key: A provider API key. A chat subscription is not an API key.

    Returns:
        Number of models returned by the provider. Model listing does not
        guarantee that every listed model supports chat or is available to use.

    Raises:
        ModelNotFound: The provider name is unknown.
        ProviderError: The catalogue request failed or the key was rejected.

    Keys use the system keyring when available, otherwise a local credentials
    file. This function makes a blocking network request.
    """
    resolved = _resolve_provider(provider)
    found = _providers.fetch_catalogue(resolved, api_key)
    _credentials.store(resolved, api_key)
    return len(found)


def signed_in(provider: str) -> bool:
    """Check for a stored or environment API key without validating it online.

    Accepts the same provider aliases as :func:`sign_in`.
    """
    return _credentials.is_signed_in(_resolve_provider(provider))


def _resolve_provider(name: str):
    mapping = {
        "claude": _providers.Provider.ANTHROPIC,
        "anthropic": _providers.Provider.ANTHROPIC,
        "chatgpt": _providers.Provider.OPENAI,
        "openai": _providers.Provider.OPENAI,
    }
    resolved = mapping.get((name or "").strip().lower())
    if resolved is None:
        raise ModelNotFound(
            f"no provider called {name!r}. Use 'claude' or 'chatgpt'.")
    return resolved


ApproveFn = Callable[[str, str, dict], bool]
"""``(tool_name, one_line_summary, arguments) -> allowed``. Expected to block."""


def _deny(name: str, summary: str, arguments: dict) -> bool:
    raise ApprovalRequired(
        f"{name} needs approval ({summary}) and no approve= callback was given. "
        "Pass an approve= callback that reviews the tool name, summary, and arguments."
    )


class Agent:
    """A model, its tools, and the loop that drives them.

    :param model: a key from :func:`models`. Local keys are bare names;
        cloud keys are ``"claude:<id>"`` or ``"chatgpt:<id>"``.
    :param workdir: the directory tools operate in. Defaults to the current
        one. This is the boundary the ``WORKSPACE`` autonomy tier enforces.
    :param system_prompt: replaces the default. See
        :mod:`hugmunn.core.prompts` for the presets the desktop app offers.
    :param tools: ``False`` (the default) gives the model no tools at all.
        ``True`` gives it the built-in set. A list of names gives it those.
    :param approve: synchronous ``(name, summary, arguments) -> bool`` callback
        for calls requiring approval under ``autonomy``. Without a callback,
        these calls raise :class:`ApprovalRequired`. Returning ``False`` denies
        the tool and yields a ``denied`` event. Higher autonomy tiers may allow
        writes without invoking this callback.
    :param effort: how hard the model works within one answer.
    :param persistence: how long the loop keeps going before it stops.
    :param autonomy: what may run without asking.
    :param skills: instruction packs to attach, by name.
    :param thinking: whether the model reasons before answering. ``None``
        uses the model's own default.
    :param start_server: for local models, start llama-server if one is not
        already running. ``False`` requires you to have started it yourself.
    :raises ModelNotFound: the model key is unknown.
    :raises HugmunnError: credentials are missing or a required server is absent.
    :raises ServerError: the local server could not start.
    :raises ValueError: a tier, tool name, or skill name is invalid.

    Construction may block while a local server loads weights. Agents retain
    history in memory and are not thread-safe. Use :meth:`save` to persist a
    library conversation; desktop conversations are saved separately by the UI.
    """

    def __init__(
        self,
        model: str,
        *,
        workdir: str = ".",
        system_prompt: str | None = None,
        tools: bool | Sequence[str] = False,
        approve: ApproveFn | None = None,
        effort: Effort | int = Effort.STANDARD,
        persistence: Persistence | int = Persistence.NORMAL,
        autonomy: Autonomy | int = Autonomy.ASK_TO_WRITE,
        skills: Sequence[str] = (),
        thinking: bool | None = None,
        start_server: bool = True,
    ) -> None:
        effort, persistence, autonomy = Effort(effort), Persistence(persistence), Autonomy(autonomy)
        if isinstance(tools, str):
            raise ValueError("tools must be a boolean or a sequence of tool names")
        if isinstance(skills, str):
            raise ValueError("skills must be a sequence of skill names")
        catalogue = _skills.load_all()
        requested_skills = set(skills)
        unknown_skills = requested_skills - {s.key for s in catalogue}
        if unknown_skills:
            raise ValueError(f"Unknown skills: {', '.join(sorted(unknown_skills))}")
        chosen = [s for s in catalogue if s.key in requested_skills]
        self.model = self._resolve(model)
        self.workdir = str(Path(workdir).expanduser().resolve())
        self.history: list[dict[str, Any]] = []
        self._approve = approve or _deny
        self._server: ServerManager | None = None
        self._closed = False
        self._effort = effort

        allowed = None
        if tools is False:
            use_tools = False
        elif tools is True:
            use_tools = True
        else:
            use_tools, allowed = True, set(tools)
            unknown = allowed - set(tool_names()) - {"recall", "spawn_agent"}
            if unknown:
                raise ValueError(f"Unknown tools: {', '.join(sorted(unknown))}")

        client = self._client(start_server)
        self._agent = _agent.Agent(
            client=client,
            workdir=self.workdir,
            system_prompt=system_prompt if system_prompt is not None
            else _config.Settings().system_prompt,
            use_tools=use_tools,
            active_skills=chosen,
            effort=Effort(effort),
            persistence=Persistence(persistence),
            autonomy=Autonomy(autonomy),
            tool_allowlist=allowed,
            thinking=thinking,
        )

    # ------------------------------------------------------------- setting up

    @staticmethod
    def _resolve(key: str) -> Model:
        for candidate in models():
            if candidate.key == key:
                return candidate
        raise ModelNotFound(
            f"no model called {key!r}. hugmunn.models() lists them; "
            "cloud keys use 'claude:<model-id>' or 'chatgpt:<model-id>'.")

    def _client(self, start_server: bool):
        if not self.model.is_local:
            resolved = _resolve_provider(self.model.provider)
            api_key = _credentials.load(resolved)
            if not api_key:
                raise HugmunnError(
                    f"not signed in to {self.model.provider}. Call "
                    f"hugmunn.sign_in({self.model.provider!r}, key) first.")
            from .core import cloud

            spec = _providers.by_key(
                f"{resolved.value}:{self.model.key.split(':', 1)[1]}")
            return cloud.build(spec, api_key, effort_level=int(self._effort))

        from .core.client import LlamaClient

        spec = _config.by_key(self.model.key)
        if spec is None:
            raise ModelNotFound(self.model.key)
        client = LlamaClient(spec.base_url)
        if not client.is_ready():
            if not start_server:
                raise HugmunnError(
                    f"{spec.label} is not running on port {spec.port} and "
                    f"start_server=False.")
            self._server = ServerManager()
            self._server.start(spec)
        return client

    # ------------------------------------------------------------- using it

    def run(self, message: str, cancel: Any = None) -> Iterator[Event]:
        """Append a user message and yield events until the turn ends.

        Args:
            message: User text to append to the current conversation.
            cancel: Optional object with ``is_set()``, such as
                :class:`threading.Event`. Cancellation is cooperative.

        Yields:
            Event: Content, reasoning, tool activity, notices, errors, or done.
                Consume the iterator fully so history records the complete turn.

        Raises:
            HugmunnError: This agent has already been closed.
            ApprovalRequired: A tool requires approval without a callback.

        Model failures normally arrive as ``error`` events. Unlike :meth:`ask`,
        this method does not convert those events into exceptions.
        """
        if self._closed:
            raise HugmunnError("this agent has been closed")
        self.history.append({"role": "user", "content": message})
        for event in self._agent.run(self.history, self._approve, cancel=cancel):
            yield Event(
                kind=event.kind, text=event.text, tool=event.tool_name,
                summary=event.tool_summary, id=event.tool_id,
                timings=event.timings or None,
            )

    def ask(self, message: str, cancel: Any = None) -> str:
        """Return concatenated answer text, raising ``HugmunnError`` on error events.

        ``message`` and ``cancel`` have the same meaning as in :meth:`run`.
        Reasoning, tool events, and notices are omitted from the returned string.
        Use :meth:`run` when the caller needs to display that activity.
        """
        parts = []
        for event in self.run(message, cancel=cancel):
            if event.kind == "content":
                parts.append(event.text)
            elif event.kind == "error":
                raise HugmunnError(event.text)
        return "".join(parts)

    def reset(self) -> None:
        """Forget the conversation. Settings and the server are untouched."""
        self.history.clear()

    # -------------------------------------------------------------- sessions

    def save(self, path: str | None = None) -> str:
        """Write a JSON conversation and return its path.

        ``path=None`` creates a new entry in the configured sessions directory.
        Each call creates a new session ID. An explicit path is overwritten;
        its parent directory must exist. Messages and model metadata are saved,
        but this method does not save the agent's run settings.

        Raises:
            OSError: The destination cannot be written.
        """
        import time

        session = _sessions.Session(
            id=_sessions.new_id(), started=time.time(), updated=time.time(),
            messages=list(self.history), model_key=self.model.key,
            model_label=self.model.label, provider=self.model.provider,
        )
        import json
        from dataclasses import asdict

        session.closed_cleanly = True
        target = Path(path).expanduser() if path is not None else session.path
        if path is None:
            target.parent.mkdir(parents=True, exist_ok=True)
        # Replace atomically so a failed write does not truncate an existing chat.
        import tempfile

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(asdict(session), output, indent=1)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return str(target)

    def load(self, path: str) -> int:
        """Replace history from a JSON session and return the message count.

        The agent keeps its current model, tools, and settings; stored metadata
        does not reconfigure it. Raises ``HugmunnError`` for an unreadable file.
        """
        from pathlib import Path

        session = _sessions.load(Path(path).expanduser())
        if session is None:
            raise HugmunnError(f"could not read a conversation from {path}")
        self.history = list(session.messages)
        return len(self.history)

    # ------------------------------------------------------------- teardown

    def close(self) -> None:
        """Stop the server this agent started. Idempotent.

        A server that was already running when the agent was created is left
        alone -- it was somebody else's decision to start it.
        """
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._closed = True

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<hugmunn.Agent model={self.model.key!r} turns={len(self.history)}>"


#: Saved conversations. Re-exported so callers can list and load them without
#: reaching into ``hugmunn.core``.
Session = _sessions.Session


def sessions(limit: int = 50) -> list[Session]:
    """Return up to ``limit`` nonempty saved sessions, newest first.

    Unreadable files are skipped. The directory follows ``HUGMUNN_CONFIG_DIR``.
    ``limit`` must be nonnegative; zero returns an empty list.
    """
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    return _sessions.recent(limit)


@contextlib.contextmanager
def agent(model: str, **kwargs: Any) -> Iterator[Agent]:
    """Yield an :class:`Agent` and close it when the context exits.

    ``model`` and keyword arguments are passed to :class:`Agent`. Cleanup also
    runs if the body raises an exception.
    """
    instance = Agent(model, **kwargs)
    try:
        yield instance
    finally:
        instance.close()
