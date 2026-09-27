"""Remote login, password hashing, expiring sessions and access throttling.

The desktop requires username/password authentication. Password verifiers live
in a separate owner-only file; cookies are memory-only and revoked on restart.
Legacy programmatic clients can still request explicit bearer-token mode.
Loopback is the default; network exposure is configured on the desktop.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

#: 32 bytes, URL-safe. Long enough that guessing is not the attack; the rate
#: limit exists for the case where something else leaks it.
TOKEN_BYTES = 32

#: Failed attempts allowed per address before it is refused for a while.
MAX_FAILURES = 10
FAILURE_WINDOW = 60.0
LOCKOUT = 300.0

DEFAULT_PORT = 8770


@dataclass(frozen=True)
class Credentials:
    """A salted password verifier; plaintext is never written to disk."""

    username: str
    salt: str
    digest: str

    @classmethod
    def create(cls, username: str, password: str):
        username = username.strip()
        if not username or len(username) > 64 or any(c.isspace() for c in username):
            raise ValueError("Use a username of 1–64 characters without spaces.")
        if not 12 <= len(password) <= 1024:
            raise ValueError("Use a password of 12–1024 characters.")
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=16384, r=8, p=1).hex()
        return cls(username, salt, digest)

    def matches(self, username: str, password: str) -> bool:
        if len(password) > 1024 or len(username) > 64:
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(self.salt),
                                n=16384, r=8, p=1).hex()
        return token_matches(username, self.username) & token_matches(digest, self.digest)

    def save(self):
        from ..config import config_dir
        root = config_dir()
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=root, prefix=".remote-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(vars(self), stream)
            os.replace(name, root / "remote-auth.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @classmethod
    def load(cls):
        from ..config import config_dir
        try:
            data = json.loads((config_dir() / "remote-auth.json").read_text())
            value = cls(**data)
            if not isinstance(value.username, str) or not value.username.strip():
                return None
            if len(bytes.fromhex(value.salt)) != 16 or len(bytes.fromhex(value.digest)) != 64:
                return None
            return value
        except (OSError, TypeError, ValueError):
            return None


class Sessions:
    """Expiring browser sessions. Tokens are memory-only and individually revocable."""

    def __init__(self, lifetime=12 * 3600):
        self.lifetime = lifetime
        self._items = {}
        self._lock = threading.Lock()

    def create(self):
        with self._lock:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if v > now}
            if len(self._items) >= 32:
                del self._items[next(iter(self._items))]
            token = new_token()
            self._items[token] = now + self.lifetime
            return token

    def valid(self, token):
        with self._lock:
            return self._items.get(token, 0) > time.monotonic()

    def revoke(self, token):
        with self._lock:
            self._items.pop(token, None)

    def clear(self):
        with self._lock:
            self._items.clear()


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_matches(supplied: str, expected: str) -> bool:
    """Constant-time comparison.

    A plain ``==`` returns as soon as two bytes differ, which leaks the
    position of the first mismatch to anyone who can time the response. That
    is a real attack on a remote endpoint even though it sounds theoretical.
    """
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


@dataclass
class RateLimiter:
    """Per-address failure counting, so a found port is not a free oracle."""

    max_failures: int = MAX_FAILURES
    window: float = FAILURE_WINDOW
    lockout: float = LOCKOUT
    _failures: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _locked: dict[str, float] = field(default_factory=dict)

    def is_locked(self, address: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        until = self._locked.get(address)
        if until is None:
            return False
        if now >= until:
            del self._locked[address]
            self._failures.pop(address, None)
            return False
        return True

    def record_failure(self, address: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        recent = self._failures[address]
        recent.append(now)
        while recent and now - recent[0] > self.window:
            recent.popleft()
        if len(recent) >= self.max_failures:
            self._locked[address] = now + self.lockout

    def record_success(self, address: str) -> None:
        self._failures.pop(address, None)
        self._locked.pop(address, None)


@dataclass(frozen=True)
class Exposure:
    """What turning this on actually means, in words the user can act on."""

    host: str
    port: int
    tunnelled: bool = False

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    @property
    def is_public_interface(self) -> bool:
        return self.host in ("0.0.0.0", "::")

    def warning(self) -> str:
        """The sentence that belongs next to the switch."""
        if self.is_loopback:
            return ("Reachable only from this machine. To use it from another "
                    "device, run a tunnel — nothing is exposed until you do.")
        if self.is_public_interface:
            return ("Reachable from every network this machine is on. Anyone "
                    "who reaches the port and has the token can run commands "
                    "here. Prefer loopback plus a tunnel.")
        return (f"Reachable at {self.host}. Anyone on that network who has the "
                f"token can run commands on this machine.")


#: How to reach the machine from elsewhere. Not implemented here on purpose:
#: hugmunn should not be in the business of running a tunnel daemon, and
#: both of these do it better and are already audited.
TUNNELS = (
    (
        "Tailscale",
        "tailscale serve --bg {port}",
        "A private network between your own devices. Nothing is published "
        "publicly and there is no URL to leak — the safest option, and the "
        "right one for a phone you own.",
        "https://tailscale.com/download",
    ),
    (
        "Cloudflare Tunnel",
        "cloudflared tunnel --url http://127.0.0.1:{port}",
        "Gives a public HTTPS URL. Convenient, but the URL is on the public "
        "internet and only the token stands between it and a shell on this "
        "machine.",
        "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
    ),
)


def tunnel_options(port: int) -> list[dict[str, str]]:
    return [
        {"name": name, "command": command.format(port=port),
         "note": note, "url": url}
        for name, command, note, url in TUNNELS
    ]
