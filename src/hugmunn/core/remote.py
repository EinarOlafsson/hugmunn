"""Access tokens and the security posture for remote access.

What this exposes is not a chat window. It is an agent that can run shell
commands and write files on the machine it is hosted on, at whatever autonomy
level is set. Anyone holding the URL holds that. So the rules here are
constraints rather than defaults, and the UI states them rather than burying
them:

* **A token is always required.** There is no unauthenticated mode, not even
  on loopback — the whole point of the feature is that the port ends up
  reachable from elsewhere, and an "I'll turn auth on later" setting is one
  that never gets turned on.
* **The token is compared in constant time**, and never logged, never put in a
  page title, never echoed back in an error.
* **Loopback by default.** Reaching the machine from outside is a tunnel's
  job. Binding to a public interface is possible and takes an explicit
  argument, because typing `0.0.0.0` should feel like a decision.
* **Failures are rate-limited per address.** A 32-byte token is not
  guessable, but an endpoint that answers a thousand guesses a second is a
  gift to anyone who finds the port.
* **Approvals still happen.** Remote does not mean unattended: the same
  autonomy policy applies, and the prompt is answered from whichever client
  is watching.

The token lives with the API keys, not in ``settings.json`` — same reasoning,
and the same file mode.
"""

from __future__ import annotations

import hmac
import secrets
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
