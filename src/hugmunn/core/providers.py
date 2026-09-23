"""Who serves a model: this machine, Anthropic, or OpenAI.

The model picker is two levels — provider first, then the model within it —
because the three lists have nothing to do with each other. A local model is
chosen by what fits in 24 GB of VRAM; a cloud model is chosen by price and
capability. Flattening them into one dropdown of thirty entries would make
both choices harder.

The catalogue here is a *fallback*, not the authority. Both providers expose
their own list, and :func:`fetch_catalogue` replaces this one as soon as the
user signs in — so a model released after this file was written still shows up,
and one the account cannot reach does not. Hardcoding the list would mean
shipping a lie with a shelf life.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

import httpx

from ..errors import HugmunnError

ANTHROPIC_API = "https://api.anthropic.com/v1"
OPENAI_API = "https://api.openai.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class Provider(str, Enum):
    LOCAL = "local"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


LABELS = {
    Provider.LOCAL: "Local models",
    Provider.ANTHROPIC: "Claude",
    Provider.OPENAI: "ChatGPT",
}

BLURBS = {
    Provider.LOCAL: "Runs on this machine. Nothing leaves it.",
    Provider.ANTHROPIC: "Anthropic's API. Prompts and tool results are sent to Anthropic.",
    Provider.OPENAI: "OpenAI's API. Prompts and tool results are sent to OpenAI.",
}

#: Where to get a key, shown in the sign-in dialog.
CONSOLE_URLS = {
    Provider.ANTHROPIC: "https://console.anthropic.com/settings/keys",
    Provider.OPENAI: "https://platform.openai.com/api-keys",
}

KEY_PREFIX = {Provider.ANTHROPIC: "sk-ant-", Provider.OPENAI: "sk-"}

CLOUD = (Provider.ANTHROPIC, Provider.OPENAI)


@dataclass(frozen=True)
class CloudModel:
    """One model reachable over an API."""

    id: str
    label: str
    provider: Provider
    blurb: str = ""
    context: int = 200_000
    max_output: int = 8192
    thinking: bool = False   # accepts an explicit reasoning/thinking control

    @property
    def key(self) -> str:
        """Registry key. Namespaced so a cloud id cannot collide with a local one."""
        return f"{self.provider.value}:{self.id}"

    # The sidebar reads the same handful of attributes off whatever model is
    # selected. Providing them here rather than branching on type at every
    # call site keeps the widget code from growing an ``isinstance`` ladder.

    @property
    def context_tokens(self) -> int:
        return self.context

    @property
    def ram_gb(self) -> int:
        return 0

    @property
    def tools_reliable(self) -> bool:
        return True

    @property
    def download_gb(self) -> float:
        return 0.0

    def is_available(self) -> bool:
        """True once a key for this provider is stored."""
        from .credentials import is_signed_in

        return is_signed_in(self.provider)


# Shown before sign-in, and replaced by the account's real list after. Kept
# short on purpose: it exists so the dropdown is not empty, not to be a
# maintained inventory of every model either provider offers.
FALLBACK: dict[Provider, tuple[CloudModel, ...]] = {
    Provider.ANTHROPIC: (
        CloudModel("claude-opus-5-5", "Claude Opus 5.5", Provider.ANTHROPIC,
                   "Coding and knowledge work. Adaptive thinking.", 1_000_000, 128_000, True),
        CloudModel("claude-fable-5-1", "Claude Fable 5.1", Provider.ANTHROPIC,
                   "Demanding reasoning and long-running tasks.", 1_000_000, 128_000, True),
        CloudModel("claude-sonnet-5", "Claude Sonnet 5", Provider.ANTHROPIC,
                   "Balanced speed and capability.", 1_000_000, 128_000, True),
        CloudModel("claude-opus-5", "Claude Opus 5", Provider.ANTHROPIC,
                   "Earlier Opus model.", 1_000_000, 128_000, True),
        CloudModel("claude-fable-5", "Claude Fable 5", Provider.ANTHROPIC,
                   "Earlier Fable model.", 1_000_000, 128_000, True),
        CloudModel("claude-haiku-4-5-20251001", "Claude Haiku 4.5", Provider.ANTHROPIC,
                   "Lower latency and cost.", 200_000, 64_000, True),
    ),
    Provider.OPENAI: (
        CloudModel("gpt-6-astra", "GPT-6 Astra", Provider.OPENAI,
                   "Complex reasoning, coding and research.", 1_050_000, 128_000, True),
        CloudModel("gpt-6-sol", "GPT-6 Sol", Provider.OPENAI,
                   "Coding and multi-step tasks.", 1_050_000, 128_000, True),
        CloudModel("gpt-6-luna", "GPT-6 Luna", Provider.OPENAI,
                   "Lower-cost, focused tasks.", 1_050_000, 128_000, True),
        CloudModel("gpt-5.1", "GPT-5.1", Provider.OPENAI,
                   "Earlier reasoning model.", 400_000, 32_000, True),
        CloudModel("gpt-4.1", "GPT-4.1", Provider.OPENAI,
                   "Non-reasoning model.", 1_000_000, 32_000, False),
    ),
}

#: Filled by :func:`fetch_catalogue`; falls back per-provider when empty.
_LIVE: dict[Provider, tuple[CloudModel, ...]] = {}


def models_for(provider: Provider) -> tuple[CloudModel, ...]:
    """Every model this provider offers, live list preferred."""
    if provider == Provider.LOCAL:
        return ()
    return _LIVE.get(provider) or FALLBACK.get(provider, ())


def set_catalogue(provider: Provider, models: tuple[CloudModel, ...]) -> None:
    if models:
        _LIVE[provider] = models


def has_live_catalogue(provider: Provider) -> bool:
    """Whether this provider's real list has been fetched this session.

    The shipped list is a placeholder so the dropdown is not empty. Anything
    relying on it is showing the user models chosen when this file was
    written, which is not the same as the models their key can reach.
    """
    return bool(_LIVE.get(provider))


def clear_catalogue(provider: Provider) -> None:
    _LIVE.pop(provider, None)


def by_id(model_id: str) -> CloudModel | None:
    for provider in CLOUD:
        for model in models_for(provider):
            if model.id == model_id:
                return model
    return None


def by_key(key: str) -> CloudModel | None:
    """Look up by the namespaced ``provider:id`` registry key."""
    if ":" not in key:
        return None
    prefix, _, model_id = key.partition(":")
    try:
        provider = Provider(prefix)
    except ValueError:
        return None
    return next((m for m in models_for(provider) if m.id == model_id), None)


# ------------------------------------------------------------ live listing


def _pretty(model_id: str) -> str:
    """A readable label for an id the provider gave us and we have no name for."""
    known = {m.id: m.label for group in FALLBACK.values() for m in group}
    if model_id in known:
        return known[model_id]
    text = model_id.replace("-latest", "")
    # Strip a trailing yyyymmdd date stamp; it belongs in the tooltip, not
    # in a dropdown the user reads at a glance.
    parts = text.split("-")
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        parts = parts[:-1]
    return " ".join(p.upper() if len(p) <= 3 and p.isalpha() else p.capitalize()
                    for p in parts)


def _thinks(model_id: str, provider: Provider) -> bool:
    """Whether an explicit reasoning control is worth sending.

    Wrong in the safe direction either way: a model that ignores the
    parameter loses nothing, and one that would have accepted it simply
    runs at its default. The known exception is OpenAI's non-reasoning
    families, which *reject* the parameter outright rather than ignoring it.
    """
    if provider == Provider.ANTHROPIC:
        return not model_id.startswith(("claude-3", "claude-2"))
    return not model_id.startswith(("gpt-4", "gpt-3", "chatgpt", "text-", "dall", "whisper", "tts"))


def _usable(model_id: str, provider: Provider) -> bool:
    """Filter the chat models out of a list that also has embeddings and audio."""
    if provider == Provider.ANTHROPIC:
        return model_id.startswith("claude")
    return model_id.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")) and not any(
        marker in model_id
        for marker in ("embedding", "audio", "realtime", "tts", "whisper",
                       "image", "moderation", "transcribe", "search", "instruct", "live", "codex", "pro")
    )


def fetch_catalogue(provider: Provider, api_key: str, timeout: float = 15.0) -> tuple[CloudModel, ...]:
    """Ask the provider what this key can actually reach.

    Raises :class:`ProviderError` with something readable — an expired key
    and an unreachable network are different problems and the dialog says
    which. On success the result is installed as the live catalogue.
    """
    if provider == Provider.ANTHROPIC:
        url = f"{ANTHROPIC_API}/models?limit=200"
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        url = f"{OPENAI_API}/models"
        headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach {LABELS[provider]}: {exc}") from exc

    if response.status_code in (401, 403):
        raise ProviderError(
            f"{LABELS[provider]} rejected that key ({response.status_code}). "
            "Check it was copied whole, and that it has not been revoked."
        )
    if response.status_code >= 400:
        raise ProviderError(
            f"{LABELS[provider]} returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )

    try:
        rows = response.json().get("data") or []
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        raise ProviderError(f"{LABELS[provider]} sent a response we could not read") from exc

    defaults = {m.id: m for m in FALLBACK.get(provider, ())}
    models = []
    for row in rows:
        model_id = row.get("id") or ""
        if not model_id or not _usable(model_id, provider):
            continue
        known = defaults.get(model_id)
        models.append(CloudModel(
            id=model_id,
            label=row.get("display_name") or _pretty(model_id),
            provider=provider,
            blurb=known.blurb if known else f"From your {LABELS[provider]} account.",
            context=row.get("max_input_tokens") or (known.context if known else (200_000 if provider == Provider.ANTHROPIC else 128_000)),
            max_output=row.get("max_tokens") or (known.max_output if known else 8192),
            thinking=known.thinking if known else _thinks(model_id, provider),
        ))

    if not models:
        raise ProviderError(
            f"{LABELS[provider]} listed no chat models for this key. It may be "
            "scoped to something else."
        )
    # Newest first is what both APIs return; keep it rather than sorting
    # alphabetically, which would bury the current flagship under old ones.
    result = tuple(models)
    set_catalogue(provider, result)
    return result


class ProviderError(HugmunnError):
    """A provider rejected credentials or could not return its model catalogue."""
