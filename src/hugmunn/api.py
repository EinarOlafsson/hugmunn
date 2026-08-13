"""The public Python interface. Everything else in this package is internal.

``import hugmunn`` gets you this module's names and nothing more. The modules
under ``hugmunn.core`` and ``hugmunn.ui`` are implementation: they change
without notice, and importing from them directly is not supported. Anything
here keeps its meaning across a minor version, and anything removed goes
through a release where it still works and warns.

The shape is one class and a few functions::

    import hugmunn

    agent = hugmunn.Agent(model="uncensored-gemma")
    for event in agent.run("what does config.py do?"):
        if event.kind == "content":
            print(event.text, end="")

That starts a llama.cpp server if one is not already up, streams the answer,
and stops the server when the agent is closed. A cloud model is the same call
with a different name::

    agent = hugmunn.Agent(model="claude:claude-opus-5")

Tools are off unless asked for, and when they are on, anything that changes
the machine goes through ``approve`` -- which defaults to refusing. That is
deliberate: a library that runs shell commands the moment it is imported into
somebody's script is a library that gets one bad review and deserves it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

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
    "Agent", "Event", "Model", "Session",
    "Effort", "Persistence", "Autonomy",
    "models", "available_models", "sign_in", "signed_in",
    "skills", "tool_names",
    "ApprovalRequired", "ModelNotFound", "ServerError", "HugmunnError",
    "__version__",
]

from . import __version__  # noqa: E402  (re-exported deliberately)

#: Tiers. Re-exported so callers never import from ``hugmunn.core``.
Effort = _effort.Effort
Persistence = _persistence.Persistence
Autonomy = _autonomy.Autonomy


class HugmunnError(RuntimeError):
    """Base class for everything this package raises deliberately."""


class ModelNotFound(HugmunnError):
    """The named model is not in the registry, or its provider is unknown."""


class ApprovalRequired(HugmunnError):
    """A tool needed a human and no ``approve`` callback said yes.

    Raised rather than returned, because a caller who did not pass ``approve``
    almost certainly did not mean to run a shell command, and a silent refusal
    buried in a stream is a bug report waiting to happen.
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
    """A model this installation can run.

    ``key`` is what :class:`Agent` takes. For local models it is a short name
    such as ``"uncensored-gemma"``; for cloud models it is
    ``"claude:<id>"`` or ``"chatgpt:<id>"``.
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
    """Every model this installation knows about.

    ``provider`` filters to ``"local"``, ``"claude"`` or ``"chatgpt"``.
    Cloud lists come from the account when a key is present and from a small
    built-in list when it is not, so this is cheap and never blocks.
    """
    out = [_wrap_local(s) for s in _config.REGISTRY]
    for cloud in _providers.CLOUD:
        out.extend(_wrap_cloud(m) for m in _providers.models_for(cloud))
    return [m for m in out if provider is None or m.provider == provider]


def available_models() -> list[Model]:
    """Models that could be run right now -- weights present, or signed in."""
    return [m for m in models()
            if (m.is_local and _config.by_key(m.key)
                and _config.by_key(m.key).is_available()) or (not m.is_local and m.downloaded)]


def skills() -> list[str]:
    """Names of the instruction packs that can be attached to an agent."""
    return [s.key for s in _skills.load_all()]


def tool_names() -> list[str]:
    """Every built-in tool an agent may be given."""
    return sorted(_tools.by_name())


def sign_in(provider: str, api_key: str) -> int:
    """Store an API key and load that provider's real model list.

    Returns how many models the key can reach. Raises
    :class:`~hugmunn.core.providers.ProviderError` if the key is rejected --
    which is the point of doing the round trip rather than just saving it.
    """
    resolved = _resolve_provider(provider)
    found = _providers.fetch_catalogue(resolved, api_key)
    _credentials.store(resolved, api_key)
    return len(found)


def signed_in(provider: str) -> bool:
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
        f"Pass approve=lambda name, summary, args: True to allow everything, "
        f"or lower the autonomy tier."
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
    :param approve: called before anything that changes the machine. Omitted,
        any such call raises :class:`ApprovalRequired`.
    :param effort: how hard the model works within one answer.
    :param persistence: how long the loop keeps going before it stops.
    :param autonomy: what may run without asking.
    :param skills: instruction packs to attach, by name.
    :param thinking: whether the model reasons before answering. ``None``
        uses the model's own default.
    :param start_server: for local models, start llama-server if one is not
        already running. ``False`` requires you to have started it yourself.
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
        self.model = self._resolve(model)
        self.workdir = workdir
        self.history: list[dict[str, Any]] = []
        self._approve = approve or _deny
        self._server: ServerManager | None = None
        self._closed = False

        allowed = None
        if tools is False:
            use_tools = False
        elif tools is True:
            use_tools = True
        else:
            use_tools, allowed = True, set(tools)

        client = self._client(start_server)
        chosen = [s for s in _skills.load_all() if s.key in set(skills)]
        self._agent = _agent.Agent(
            client=client,
            workdir=workdir,
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
            f"cloud keys look like 'claude:claude-opus-5'.")

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
            return cloud.build(spec, api_key)

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
        """Send a message and stream what comes back.

        The conversation accumulates in :attr:`history`, so consecutive calls
        continue it. ``cancel`` is anything with ``.is_set()`` --
        a :class:`threading.Event` -- and ends the turn when it becomes true.
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
        """Run a turn and return the answer as one string.

        The convenience form. Everything else -- tool calls, reasoning,
        notices -- is discarded, so use :meth:`run` when any of that matters.
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
        """Write this conversation to disk. Returns the path."""
        import time

        session = _sessions.Session(
            id=_sessions.new_id(), started=time.time(), updated=time.time(),
            messages=list(self.history), model_key=self.model.key,
            model_label=self.model.label, provider=self.model.provider,
        )
        if path:
            import json
            from dataclasses import asdict
            from pathlib import Path

            Path(path).write_text(json.dumps(asdict(session), indent=1),
                                  encoding="utf-8")
            return path
        _sessions.save(session)
        _sessions.mark_closed(session)
        return str(session.path)

    def load(self, path: str) -> int:
        """Continue a saved conversation. Returns how many messages came back."""
        from pathlib import Path

        session = _sessions.load(Path(path))
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
    """Saved conversations, newest first."""
    return _sessions.recent(limit)


@contextlib.contextmanager
def agent(model: str, **kwargs):
    """:class:`Agent` as a context manager, closed on the way out.

    ``with hugmunn.agent("code-glm") as a: print(a.ask("hello"))``
    """
    instance = Agent(model, **kwargs)
    try:
        yield instance
    finally:
        instance.close()
