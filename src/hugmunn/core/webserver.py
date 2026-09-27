"""Authenticated browser control of the desktop, without external web dependencies."""
from __future__ import annotations

import json
import ssl
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator, Protocol
from urllib.parse import parse_qs, urlparse
import time

from . import remote


class Bridge(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
    def send(self, text: str) -> Iterator[dict[str, Any]]: ...
    def resolve_approval(self, call_id: str, allowed: bool) -> bool: ...
    def apply_setting(self, key: str, value: Any) -> dict[str, Any]: ...
    def cancel(self) -> None: ...


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()


class RemoteServer:
    """Password/cookie auth for browsers; optional bearer mode for existing API users.

    Pass Credentials for desktop use. Bearer compatibility is disabled in that
    mode. Use a trusted HTTPS tunnel or certfile/keyfile for encrypted transport.
    """

    def __init__(self, bridge, host="127.0.0.1", port=remote.DEFAULT_PORT,
                 token=None, credentials=None, certfile=None, keyfile=None):
        self.bridge, self.host, self.port = bridge, host, port
        self.credentials = credentials
        self.token = token or remote.new_token()
        self.sessions = remote.Sessions()
        self.limiter = remote.RateLimiter()
        self._auth_lock = threading.Lock()
        self._server = self._thread = None
        self.certfile, self.keyfile = certfile, keyfile

    @property
    def is_running(self):
        return self._server is not None

    @property
    def exposure(self):
        return remote.Exposure(self.host, self.port)

    def url(self, host=None):
        address = host or self.host
        if address in ("0.0.0.0", "::"):
            address = "127.0.0.1"
        if ":" in address:
            address = f"[{address}]"
        url = f"{'https' if self.certfile else 'http'}://{address}:{self.port}/"
        return url if self.credentials else url + f"?t={self.token}"

    def start(self):
        if self.is_running:
            return
        server = _HTTPServer((self.host, self.port), _make_handler(self))
        try:
            if self.certfile:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(self.certfile, self.keyfile)
                server.socket = context.wrap_socket(server.socket, server_side=True)
        except Exception:
            server.server_close()
            raise
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="hugmunn-remote")
        self._thread.start()

    def stop(self):
        self.sessions.clear()
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(timeout=2)

    def rotate_token(self):
        self.sessions.clear()
        self.token = remote.new_token()
        return self.token

    def authorised(self, supplied, address):
        with self._auth_lock:
            if self.limiter.is_locked(address):
                return False, "too many failed attempts"
            if self.credentials is None and remote.token_matches(supplied, self.token):
                self.limiter.record_success(address)
                return True, ""
            self.limiter.record_failure(address)
            return False, "unauthorised"

    def login(self, username, password, address):
        with self._auth_lock:
            if self.limiter.is_locked(address):
                return None, 429
            if self.credentials and self.credentials.matches(username, password):
                self.limiter.record_success(address)
                return self.sessions.create(), 200
            self.limiter.record_failure(address)
            return None, 401


def _make_handler(owner):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "hugmunn"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *args):
            pass

        def _cookie(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie["hugmunn_session"].value if "hugmunn_session" in cookie else ""
            except Exception:
                return ""

        def _token(self):
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return header[7:].strip()
            return (parse_qs(urlparse(self.path).query).get("t") or [""])[0]

        def _check(self):
            if owner.credentials:
                if owner.sessions.valid(self._cookie()):
                    return True
                self._json({"error": "unauthorised"}, 401)
                return False
            ok, why = owner.authorised(self._token(), self.client_address[0])
            if not ok:
                self._json({"error": why}, 429 if why != "unauthorised" else 401)
            return ok

        def _headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

        def _send(self, body, content_type, status=200, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=200, extra=None):
            self._send(json.dumps(payload).encode(), "application/json", status, extra)

        def _body(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json.")
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests are not accepted.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError("Request body must be 1 byte to 1 MiB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            return body

        def _same_origin(self):
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                    return False
            # Browsers cannot add this header cross-origin without a preflight,
            # which this server does not enable. Covers login CSRF too.
            return not owner.credentials or self.headers.get("X-Hugmunn-Request") == "1"

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/health":
                self._json({"ok": True, "service": "hugmunn"})
                return
            if route == "/":
                from .webui import PAGE
                self._send(PAGE.encode(), "text/html; charset=utf-8")
                return
            if not self._check():
                return
            try:
                if route == "/api/state":
                    self._json(owner.bridge.snapshot())
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, RuntimeError) as exc:
                self._json({"error": str(exc)}, 409)
            except Exception:
                self._json({"error": "Could not read desktop state."}, 500)

        def do_POST(self):
            if not self._same_origin():
                self.close_connection = True
                self._json({"error": "cross-origin request refused"}, 403)
                return
            route = urlparse(self.path).path
            if route != "/api/login" and not self._check():
                self.close_connection = True
                return
            try:
                body = self._body()
                if route == "/api/login":
                    username, password = body.get("username"), body.get("password")
                    if not isinstance(username, str) or not isinstance(password, str):
                        raise ValueError("Username and password are required.")
                    token, status = owner.login(username, password, self.client_address[0])
                    if token:
                        cookie = f"hugmunn_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"
                        # HTTPS proxies preserve Origin; it is never used to bypass auth.
                        if owner.certfile or self.headers.get("Origin", "").startswith("https://"):
                            cookie += "; Secure"
                        self._json({"ok": True}, extra={"Set-Cookie": cookie})
                    else:
                        self._json({"error": "too many failed attempts" if status == 429 else "Invalid login."}, status)
                elif route == "/api/logout":
                    owner.sessions.revoke(self._cookie())
                    self._json({"ok": True}, extra={"Set-Cookie": "hugmunn_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                elif route == "/api/send":
                    text = body.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("A message is required.")
                    self._stream(text)
                elif route == "/api/approve":
                    if not isinstance(body.get("allowed"), bool) or not isinstance(body.get("id"), str):
                        raise ValueError("An approval ID and boolean decision are required.")
                    self._json({"answered": owner.bridge.resolve_approval(body["id"], body["allowed"])})
                elif route == "/api/setting":
                    self._json(owner.bridge.apply_setting(str(body.get("key", "")), body.get("value")))
                elif route == "/api/action":
                    self._json(owner.bridge.action(str(body.get("name", "")), body.get("value")))
                elif route == "/api/cancel":
                    owner.bridge.cancel()
                    self._json({"cancelled": True})
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, TypeError) as exc:
                self.close_connection = True
                self._json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
            except Exception:
                self.close_connection = True
                self._json({"error": "Desktop operation failed. Check the desktop for details."}, 500)

        def _stream(self, text):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-Accel-Buffering", "no")
            self._headers()
            self.end_headers()

            def write(event):
                payload = ("data: " + json.dumps(event) + "\n\n").encode()
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

            stream = owner.bridge.send(text)
            try:
                for event in stream:
                    if not owner.is_running:
                        break
                    if owner.credentials and not owner.sessions.valid(self._cookie()):
                        break
                    if owner.credentials is None and not remote.token_matches(self._token(), owner.token):
                        break
                    write(event)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception as exc:
                write({"kind": "error", "text": str(exc)})
            finally:
                stream.close()
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
