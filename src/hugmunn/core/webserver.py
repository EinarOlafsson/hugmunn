"""A small HTTP server so hugmunn can be driven from a phone.

Stdlib only. A web framework would be less code to write and one more thing to
keep patched on a machine that is, by the nature of this feature, reachable
from somewhere else — and what is needed here is four routes and an event
stream, which ``http.server`` does adequately.

Everything the browser can do goes through :class:`Bridge`, which the desktop
window implements. The server owns no application state: it holds a token, a
socket and a rate limiter, and asks the bridge for the rest. That is what
keeps the two clients honest with each other — a message sent from the phone
appears in the desktop transcript because there is only one conversation, not
because they are kept in sync.

Approvals are the interesting part. The agent runs inside the request thread
and blocks on a human answer; whichever client answers first releases it. So a
tool call started from the phone can be approved from the desk, and the
autonomy policy applies exactly as it does locally. Remote does not mean
unattended.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import parse_qs, urlparse

from . import remote


class Bridge(Protocol):
    """What the desktop window has to provide. Called from request threads."""

    def snapshot(self) -> dict[str, Any]:
        """Everything a freshly-loaded page needs to render."""

    def send(self, text: str) -> Iterator[dict[str, Any]]:
        """Run one turn, yielding event dicts as they happen."""

    def resolve_approval(self, call_id: str, allowed: bool) -> bool:
        """Answer a pending tool approval. False if it was already answered."""

    def apply_setting(self, key: str, value: Any) -> dict[str, Any]:
        """Change one setting; returns the new snapshot."""

    def cancel(self) -> None:
        """Stop the turn in flight, if any."""


@dataclass
class _Route:
    method: str
    path: str
    handler: Callable


class RemoteServer:
    """Serves the remote UI. One instance; start and stop are idempotent."""

    def __init__(self, bridge: Bridge, host: str = "127.0.0.1",
                 port: int = remote.DEFAULT_PORT, token: str | None = None) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self.token = token or remote.new_token()
        self.limiter = remote.RateLimiter()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def exposure(self) -> remote.Exposure:
        return remote.Exposure(self.host, self.port)

    def url(self, host: str | None = None) -> str:
        """The address to open, token included.

        The token is in the query string because the first request is a page
        load typed or scanned on another device, and there is nowhere to put a
        header. The page swaps it for a header immediately and rewrites the
        address bar, so it does not sit in history any longer than it must.
        """
        return f"http://{host or self.host}:{self.port}/?t={self.token}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler = _make_handler(self)
        # ThreadingHTTPServer: one request thread each, which an SSE stream
        # holds open for the length of a turn. A single-threaded server would
        # be unable to answer the approval the stream is waiting for.
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
            name="hugmunn-remote")
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def rotate_token(self) -> str:
        """Issue a new token, invalidating every open session."""
        self.token = remote.new_token()
        return self.token

    # ----------------------------------------------------------------- auth

    def authorised(self, supplied: str, address: str) -> tuple[bool, str]:
        if self.limiter.is_locked(address):
            return False, "too many failed attempts"
        if remote.token_matches(supplied, self.token):
            self.limiter.record_success(address)
            return True, ""
        self.limiter.record_failure(address)
        # Deliberately identical for a missing and a wrong token: telling them
        # apart tells an attacker whether they are close.
        return False, "unauthorised"


def _make_handler(owner: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "hugmunn"
        sys_version = ""          # do not advertise the Python version

        # ---- plumbing ----

        def log_message(self, *args) -> None:
            """Silent. Access logs would contain the token from the query."""

        @property
        def client_address_str(self) -> str:
            return self.client_address[0] if self.client_address else "?"

        def _token(self) -> str:
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return header[7:].strip()
            return (parse_qs(urlparse(self.path).query).get("t") or [""])[0]

        def _check(self) -> bool:
            ok, why = owner.authorised(self._token(), self.client_address_str)
            if not ok:
                self._json({"error": why}, status=401 if why == "unauthorised" else 429)
            return ok

        def _send(self, body: bytes, content_type: str, status: int = 200,
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # This page must never be framed or indexed, and nothing here
            # should be cached by an intermediary.
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(json.dumps(payload).encode(), "application/json", status)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return {}

        # ---- routes ----

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            route = urlparse(self.path).path
            if route == "/health":
                # No token: it reveals nothing and makes a tunnel testable.
                self._json({"ok": True, "service": "hugmunn"})
                return
            if not self._check():
                return
            if route == "/":
                from .webui import PAGE

                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif route == "/api/state":
                self._json(owner.bridge.snapshot())
            else:
                self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            route = urlparse(self.path).path
            if not self._check():
                return
            body = self._body()

            if route == "/api/send":
                self._stream(str(body.get("text") or ""))
            elif route == "/api/approve":
                answered = owner.bridge.resolve_approval(
                    str(body.get("id") or ""), bool(body.get("allowed")))
                self._json({"answered": answered})
            elif route == "/api/setting":
                self._json(owner.bridge.apply_setting(
                    str(body.get("key") or ""), body.get("value")))
            elif route == "/api/cancel":
                owner.bridge.cancel()
                self._json({"cancelled": True})
            else:
                self._json({"error": "not found"}, status=404)

        def _stream(self, text: str) -> None:
            """Run a turn, writing each event as it happens.

            Chunked rather than a length-prefixed body: the whole value is
            seeing tokens arrive, and a phone on a slow link with a blank
            screen for ninety seconds is indistinguishable from a hang.
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-Accel-Buffering", "no")   # nginx/cloudflared
            self.end_headers()

            def write(event: dict) -> None:
                payload = f"data: {json.dumps(event)}\n\n".encode()
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

            try:
                for event in owner.bridge.send(text):
                    write(event)
            except (BrokenPipeError, ConnectionResetError):
                # The phone locked or lost signal. The turn keeps running;
                # the desktop still has it, and /api/state has it on reload.
                owner.bridge.cancel()
                return
            except Exception as exc:  # noqa: BLE001 - never 500 mid-stream
                try:
                    write({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
                except OSError:
                    pass
            try:
                write({"kind": "done"})
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass

    return Handler


class PendingApprovals:
    """Tool approvals waiting on a human, answerable from any client.

    The agent runs in whichever thread started the turn and blocks here. The
    desktop dialog and the browser both resolve through the same registry, so
    the first answer wins and the other client's prompt simply disappears --
    which is the correct behaviour when the same person is holding both.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: dict[str, dict[str, Any]] = {}

    def create(self, call_id: str, name: str, summary: str,
               arguments: dict) -> threading.Event:
        gate = threading.Event()
        with self._lock:
            self._waiting[call_id] = {
                "gate": gate, "allowed": False, "name": name,
                "summary": summary, "arguments": arguments, "at": time.time(),
            }
        return gate

    def resolve(self, call_id: str, allowed: bool) -> bool:
        with self._lock:
            entry = self._waiting.get(call_id)
            if entry is None or entry["gate"].is_set():
                return False
            entry["allowed"] = allowed
            entry["gate"].set()
            return True

    def result(self, call_id: str) -> bool:
        with self._lock:
            entry = self._waiting.pop(call_id, None)
        return bool(entry and entry["allowed"])

    def outstanding(self) -> list[dict[str, Any]]:
        """What a freshly-loaded page should immediately be asked about."""
        with self._lock:
            return [
                {"id": key, "name": e["name"], "summary": e["summary"],
                 "arguments": e["arguments"]}
                for key, e in self._waiting.items() if not e["gate"].is_set()
            ]

    def release_all(self, allowed: bool = False) -> None:
        """Unblock everything, denying by default. For shutdown and cancel."""
        with self._lock:
            for entry in self._waiting.values():
                entry["allowed"] = allowed
                entry["gate"].set()
