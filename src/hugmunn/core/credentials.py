"""Where an API key is kept.

The system keyring when there is one — GNOME Keyring, KWallet, macOS Keychain,
Windows Credential Manager — because that is the only place a secret is
encrypted at rest and not readable by every process running as this user.

When there is not one, a file at ``~/.config/hugmunn/credentials.json``,
mode ``0600``, and the UI says so rather than implying the key is protected.
A headless Linux box with no D-Bus session is the common case for that, and
refusing to store the key at all there would mean retyping it every launch,
which in practice means pasting it into a shell history instead.

The key is never written into ``settings.json``. That file is the one a user
would plausibly copy between machines, paste into a bug report, or commit.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .providers import Provider

SERVICE = "hugmunn"


def _cred_file() -> Path:
    """Where the key file lives, resolved on each call.

    This was computed once at import, which meant it ignored a config
    directory set afterwards -- so the file backend wrote to whatever the
    environment happened to say when the module first loaded. Invisible on a
    machine with a working keyring, because the keyring ignores the path
    entirely; obvious the moment you run somewhere without one.
    """
    from ..config import config_dir

    return config_dir() / "credentials.json"


def _keyring():
    """The keyring module, if it is installed *and* has a working backend.

    An import alone is not enough: ``keyring`` always imports and falls back
    to a ``fail.Keyring`` backend that raises on every call. Storing into
    that would report success and lose the key.
    """
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def backend_name() -> str:
    """What the UI tells the user their key is stored in."""
    keyring = _keyring()
    if keyring is None:
        return f"a file at {_cred_file()} (mode 600)"
    try:
        return type(keyring.get_keyring()).__name__
    except Exception:
        return "the system keyring"


def is_secure() -> bool:
    """True when the key lands in a real keyring rather than a plain file."""
    return _keyring() is not None


# ------------------------------------------------------------- file backend


def _read_file() -> dict[str, str]:
    try:
        data = json.loads(_cred_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _write_file(data: dict[str, str]) -> None:
    _cred_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _cred_file().with_suffix(".json.tmp")
    # Create with 0600 from the start. Writing then chmod'ing leaves a
    # window in which the key is world-readable, which is exactly the
    # window somebody's backup daemon runs in.
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _cred_file())


# ------------------------------------------------------------------- public


def store(provider: Provider, api_key: str) -> None:
    """Save a key. Empty or whitespace-only clears it instead."""
    api_key = (api_key or "").strip()
    if not api_key:
        clear(provider)
        return
    keyring = _keyring()
    if keyring is not None:
        try:
            keyring.set_password(SERVICE, provider.value, api_key)
            return
        except Exception:
            pass  # backend disappeared mid-session; fall through to the file
    data = _read_file()
    data[provider.value] = api_key
    _write_file(data)


def load(provider: Provider) -> str:
    """The stored key, or ``""``.

    An environment variable wins over stored state, so a CI run or a shell
    that already exports ``ANTHROPIC_API_KEY`` does not need the dialog at
    all — and so a user can override a stale stored key without hunting for
    where it was saved.
    """
    env = os.environ.get(
        "ANTHROPIC_API_KEY" if provider == Provider.ANTHROPIC else "OPENAI_API_KEY", ""
    ).strip()
    if env:
        return env
    keyring = _keyring()
    if keyring is not None:
        try:
            return (keyring.get_password(SERVICE, provider.value) or "").strip()
        except Exception:
            pass
    return _read_file().get(provider.value, "").strip()


def clear(provider: Provider) -> None:
    keyring = _keyring()
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE, provider.value)
        except Exception:
            pass
    data = _read_file()
    if data.pop(provider.value, None) is not None:
        _write_file(data)


def is_signed_in(provider: Provider) -> bool:
    return bool(load(provider))


def masked(provider: Provider) -> str:
    """The key as it is safe to show on screen: ``sk-ant-…4f2a``."""
    key = load(provider)
    if not key:
        return ""
    if len(key) < 12:
        return "…"
    head = key[:7] if key.startswith("sk-ant-") else key[:3]
    return f"{head}…{key[-4:]}"


def from_env(provider: Provider) -> bool:
    """True when the active key came from the environment, not from storage."""
    name = "ANTHROPIC_API_KEY" if provider == Provider.ANTHROPIC else "OPENAI_API_KEY"
    return bool(os.environ.get(name, "").strip())
