"""Minimal public error reports, gated by agreement and a selected GitHub account.

Raw exception messages and application logs never enter the report payload.
GitHub CLI owns authentication; Hugmunn does not store another copy of its token.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from datetime import date

from .._version import __version__
from ..config import Settings, config_dir
from .onboarding import needs_setup

REPOSITORY = "EinarOlafsson/hugmunn"
CATEGORIES = {"unexpected", "model-request", "model-start", "model-download", "runtime-setup"}
_LOCK = threading.Lock()
_PENDING = threading.BoundedSemaphore(1)


def _gh_api(endpoint: str, payload: dict | None = None):
    """Call GitHub through gh without putting tokens in arguments or settings."""
    if "pytest" in sys.modules or os.environ.get("HUGMUNN_PYTEST_SESSION"):
        raise RuntimeError("Live GitHub requests are disabled during tests")
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("Install GitHub CLI to connect your account")
    command = [gh, "api", "--hostname", "github.com", endpoint]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload else None,
                            capture_output=True, text=True, timeout=12)
    if result.returncode:
        # CLI output may contain authentication details. Never surface it in reports.
        raise RuntimeError("GitHub request failed; check your sign-in and network connection")
    return json.loads(result.stdout)


def github_account() -> str:
    """Verify the CLI's current GitHub account; callers run this off the GUI thread."""
    login = _gh_api("user").get("login", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
        raise RuntimeError("GitHub did not return a valid account name")
    return login


def make_report(category: str, exc: BaseException | None = None) -> dict[str, str]:
    """Build an allowlisted report with no exception text, locals, paths or logs."""
    category = category if category in CATEGORIES else "unexpected"
    lines = [f"Hugmunn: {__version__}", f"OS: {platform.system()}",
             f"Python: {platform.python_version()}", f"Category: {category}"]
    if exc is not None:
        cls = type(exc)
        name = cls.__name__ if getattr(builtins, cls.__name__, None) is cls else "Exception"
        lines.append(f"Exception: {name}")
        tb = exc.__traceback__
        while tb is not None:
            module = tb.tb_frame.f_globals.get("__name__", "")
            if re.fullmatch(r"hugmunn(?:\.[A-Za-z_][A-Za-z_0-9]*)+", module):
                lines.append(f"Location: {module}:{tb.tb_lineno}")
            tb = tb.tb_next
    diagnostic = "\n".join(lines)
    fingerprint = hashlib.sha256(diagnostic.encode()).hexdigest()[:16]
    return {"title": f"[Hugmunn error {fingerprint}] {category}",
            "body": "Automatically submitted with the user's setup preference.\n\n```text\n"
                    + diagnostic + "\n```\n\nNo message text, logs or conversation contents were collected."}


def send_report(payload: dict[str, str]) -> str:
    """Send at most three distinct reports per day after checking current consent.

    Returns a public issue URL, or an empty string when disabled, unauthenticated,
    duplicated or rate-limited. API failures raise; the background caller handles
    them without recursion or interrupting the user's work.
    """
    with _LOCK:
        settings = Settings.load()
        if needs_setup(settings) or not settings.automatic_reports or not settings.github_account:
            return ""
        ledger_path = config_dir() / "reports.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ledger = {}
        today = date.today().isoformat()
        if ledger.get("day") != today:
            ledger = {"day": today, "sent": []}
        title = payload["title"]
        if title in ledger.get("sent", []) or len(ledger.get("sent", [])) >= 3:
            return ""
        if github_account() != settings.github_account:
            return ""  # Changing gh's account never silently changes the report author.
        # Recheck an opt-out that happened while authentication was in flight.
        current = Settings.load()
        if needs_setup(current) or not current.automatic_reports or current.github_account != settings.github_account:
            return ""
        recent = _gh_api(f"repos/{REPOSITORY}/issues?state=all&per_page=100")
        existing = next((i for i in recent if i.get("title") == title), None)
        issue = existing or _gh_api(f"repos/{REPOSITORY}/issues", payload)
        ledger.setdefault("sent", []).append(title)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger), encoding="utf-8")
        tmp.replace(ledger_path)
        return issue.get("html_url", "")


def report_error(category: str, exc: BaseException | None = None) -> None:
    """Queue a bounded, best-effort report; never retain the exception/traceback."""
    # Avoid even starting a thread for normal first-run, API-only use or tests.
    settings = Settings.load()
    if needs_setup(settings) or not settings.automatic_reports or not settings.github_account:
        return
    if "pytest" in sys.modules or os.environ.get("HUGMUNN_PYTEST_SESSION"):
        return
    payload = make_report(category, exc)
    if not _PENDING.acquire(blocking=False):
        return

    def submit():
        try:
            send_report(payload)
        except Exception:
            pass  # A reporting failure must never create another report.
        finally:
            _PENDING.release()

    threading.Thread(target=submit, name="hugmunn-error-report", daemon=True).start()
