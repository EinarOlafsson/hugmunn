"""Claude Code and Codex transports using the CLIs' subscription logins.

The CLIs return answers or proposed Hugmunn tool calls. Hugmunn executes those
calls through its existing approval policy. No API keys are read or stored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Iterator

from .client import Event, ToolCall
from .providers import CloudModel, Provider, ProviderError

NAMES = {Provider.ANTHROPIC: "claude", Provider.OPENAI: "codex"}
INSTALL_URLS = {
    Provider.ANTHROPIC: "https://code.claude.com/docs/en/setup",
    Provider.OPENAI: "https://developers.openai.com/codex/cli/",
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "tool_calls": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "arguments_json": {"type": "string"}},
            "required": ["name", "arguments_json"],
        }},
    },
    "required": ["answer", "tool_calls"],
}


@dataclass(frozen=True)
class Status:
    """A CLI's local installation and subscription authentication state."""
    installed: bool
    signed_in: bool
    message: str


_STATUS: dict[Provider, Status] = {}


def executable(provider: Provider) -> str | None:
    """Find the vendor executable on PATH or in its usual native install folder."""
    name = NAMES[provider]
    found = shutil.which(name)
    if found:
        return found
    for folder in (Path.home() / ".local/bin", Path.home() / ".npm-global/bin"):
        path = folder / (name + (".exe" if os.name == "nt" else ""))
        if path.is_file():
            return str(path)
    return None


def environment() -> dict[str, str]:
    """Keep vendor login stores, but exclude API-key and alternate billing overrides."""
    blocked = {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
        "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_SIMPLE",
    }
    env = {k: v for k, v in os.environ.items() if k not in blocked}
    if getattr(sys, "frozen", False):
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = env.pop(name + "_ORIG", None)
            env.pop(name, None)
            if original:
                env[name] = original
    return env


def login_arguments(provider: Provider) -> list[str]:
    """Arguments for browser login, never the vendor's API billing login."""
    return ["auth", "login", "--claudeai"] if provider == Provider.ANTHROPIC else ["login"]


def status(provider: Provider, *, refresh: bool = False) -> Status:
    """Read cached status; refresh performs a bounded local CLI status command.

    Call refresh outside the GUI thread. Only subscription authentication is
    accepted; an API-key login is reported as disconnected.
    """
    if not refresh and provider in _STATUS:
        return _STATUS[provider]
    path = executable(provider)
    result = Status(bool(path), False, "Check sign-in" if path else f"Install {NAMES[provider]} first")
    if refresh and path and not os.environ.get("HUGMUNN_PYTEST_SESSION"):
        args = ["auth", "status", "--json"] if provider == Provider.ANTHROPIC else ["login", "status"]
        try:
            run = subprocess.run([path, *args], capture_output=True, text=True,
                                 timeout=15, env=environment())
            if provider == Provider.ANTHROPIC:
                value = json.loads(run.stdout)
                ok = bool(value.get("loggedIn") and value.get("authMethod") == "claude.ai")
            else:
                ok = run.returncode == 0 and "chatgpt" in (run.stdout + run.stderr).lower()
            result = Status(True, ok, "Connected through subscription login" if ok else "Sign in with your subscription account")
        except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
            result = Status(True, False, "Could not check login; update the CLI and try again")
    _STATUS[provider] = result
    return result


def is_signed_in(provider: Provider) -> bool:
    """Return cached subscription status without blocking the interface."""
    return status(provider).signed_in


def refresh_models(provider: Provider) -> tuple[CloudModel, ...]:
    """Use CLI defaults and Codex's local model cache; never call a models API."""
    from . import providers
    default = CloudModel("default", "CLI default", provider,
                         "Use the model selected by your installed CLI and account.", thinking=True)
    models = [default]
    if provider == Provider.OPENAI:
        cache = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "models_cache.json"
        try:
            rows = json.loads(cache.read_text(encoding="utf-8"))["models"]
            for row in rows:
                if row.get("visibility") != "list" or not isinstance(row.get("slug"), str):
                    continue
                models.append(CloudModel(row["slug"], row.get("display_name") or row["slug"], provider,
                                         "Listed by your installed Codex CLI; account access may vary.",
                                         int(row.get("context_window") or 128_000), thinking=True))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    else:
        models.extend(CloudModel(alias, label, provider, "Claude Code model alias; account access may vary.", thinking=True)
                      for alias, label in (("opus", "Claude Opus"), ("sonnet", "Claude Sonnet"),
                                           ("fable", "Claude Fable"), ("haiku", "Claude Haiku")))
    result = tuple(models)
    providers.set_catalogue(provider, result)
    return result


def _stop(process: subprocess.Popen) -> None:
    """Terminate and reap this request and its children, including silent ones."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)


def _run(argv: list[str], prompt: str, cwd: Path, cancel, timeout: float) -> tuple[int, str, str]:
    """Run with stdin and bounded spooled output; cancellation never waits for a line."""
    if os.environ.get("HUGMUNN_PYTEST_SESSION"):
        raise ProviderError("Live CLI requests are disabled in tests")
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        source.write(prompt.encode("utf-8")); source.seek(0)
        process = subprocess.Popen(argv, stdin=source, stdout=out, stderr=err,
                                   cwd=cwd, env=environment(), start_new_session=os.name != "nt")
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    _stop(process)
                    return -1, "", "cancelled"
                if time.monotonic() > deadline:
                    raise ProviderError("CLI request timed out. Check the CLI login and try again.")
                if max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > 16_000_000:
                    raise ProviderError("CLI output exceeded the request limit")
                time.sleep(0.05)
            out.seek(0); err.seek(0)
            return process.returncode, out.read(16_000_000).decode("utf-8", "replace"), err.read(16_000).decode("utf-8", "replace")
        finally:
            _stop(process)


class CliClient:
    """One CLI request per Hugmunn model turn, with full conversation context.

    The final structured response is emitted when the CLI finishes. Native
    tools/customizations are disabled or sandboxed; proposed Hugmunn calls
    return to the agent's existing tool validation and approval path.
    """
    def __init__(self, model: CloudModel, effort_level: int = 2, timeout: float = 600):
        self.model = model
        self.effort_level = effort_level
        self.timeout = timeout

    def _arguments(self, folder: Path) -> list[str]:
        provider = self.model.provider
        path = executable(provider)
        if not path:
            raise ProviderError(f"Install {NAMES[provider]} and sign in from Accounts first.")
        effort = ("low", "medium", "high", "xhigh")[max(0, min(3, self.effort_level - 1))]
        if provider == Provider.ANTHROPIC:
            argv = [path, "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
                    "--tools", "", "--safe-mode", "--setting-sources", "", "--no-session-persistence",
                    "--permission-prompts", "none", "--effort", effort]
        else:
            schema = folder / "response-schema.json"
            schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
            argv = [path, "exec", "--json", "--output-schema", str(schema),
                    "--output-last-message", str(folder / "answer.json"), "--sandbox", "read-only",
                    "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                    "--color", "never", "-c", 'forced_login_method="chatgpt"',
                    "-c", 'model_provider="openai"', "-c", 'approval_policy="never"',
                    "-c", 'web_search="disabled"', "-c", f'model_reasoning_effort="{effort}"']
            for feature in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use",
                            "hooks", "plugins", "skill_search", "multi_agent", "image_generation"):
                argv += ["--disable", feature]
        if self.model.id != "default":
            argv += ["--model", self.model.id]
        if provider == Provider.OPENAI:
            argv.append("-")
        return argv

    def stream(self, messages, tools=None, temperature=None, max_tokens=4096,
               cancel=None, thinking=None) -> Iterator[Event]:
        """Return an answer or validated tool proposals, never execute native tools.

        Temperature/max_tokens/thinking are accepted for the common client
        interface; generation limits are managed by the vendor CLI. Effort is
        selected at construction. Cancellation terminates the owned process.
        """
        if cancel is not None and cancel.is_set():
            yield Event("done", text="cancelled"); return
        prompt = (
            "You are the model transport for Hugmunn. Respond to the conversation below. "
            "Use only the supplied Hugmunn tool schemas, by proposing tool_calls in the response JSON. "
            "Do not use native CLI tools, inspect this machine, or act outside this response. "
            "Hugmunn will execute approved calls and supply results in the next request. "
            "For each proposed call, arguments_json must encode an object matching its tool schema. "
            "When no tool is needed, return the answer and an empty tool_calls array.\n"
            + json.dumps({"conversation": messages, "available_tools": tools or []}, ensure_ascii=False)
        )
        try:
            if not status(self.model.provider, refresh=True).signed_in:
                raise ProviderError(f"Sign in to {NAMES[self.model.provider]} with your subscription account first.")
            with tempfile.TemporaryDirectory(prefix="hugmunn-cli-") as directory:
                folder = Path(directory)
                code, stdout, stderr = _run(self._arguments(folder), prompt, folder, cancel, self.timeout)
                if code == -1 and stderr == "cancelled":
                    yield Event("done", text="cancelled"); return
                if code:
                    raise ProviderError(f"{NAMES[self.model.provider]} exited with status {code}. "
                                        "Check its login, model access and usage limits in a terminal; update the CLI if needed.")
                if self.model.provider == Provider.ANTHROPIC:
                    result = json.loads(stdout)
                    if result.get("is_error"):
                        raise ProviderError("Claude Code could not complete the request. Check its login and usage limits.")
                    payload = result.get("structured_output")
                    if payload is None:
                        payload = json.loads(result.get("result", ""))
                else:
                    # Do not treat a failed turn or a native command as a successful answer.
                    for line in stdout.splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") in ("turn.failed", "error"):
                            raise ProviderError("Codex could not complete the request. Check its login and usage limits.")
                        if event.get("item", {}).get("type") in ("command_execution", "file_change", "mcp_tool_call"):
                            raise ProviderError("Codex attempted a native tool; this request was stopped. Update the CLI.")
                    payload = json.loads((folder / "answer.json").read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str) or not isinstance(payload.get("tool_calls"), list):
                    raise ValueError("invalid response envelope")
                allowed = {t["function"]["name"] for t in tools or []}
                calls = []
                for call in payload["tool_calls"]:
                    args = json.loads(call["arguments_json"])
                    if call["name"] not in allowed or not isinstance(args, dict):
                        raise ValueError("invalid tool proposal")
                    calls.append(ToolCall("cli_" + uuid.uuid4().hex, call["name"], json.dumps(args)))
                if payload["answer"]:
                    yield Event("content", text=payload["answer"])
                if calls:
                    yield Event("tool_calls", tool_calls=calls)
                yield Event("done")
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ProviderError) as exc:
            text = str(exc) if isinstance(exc, ProviderError) else "The CLI returned an invalid or incomplete response. Update it and try again."
            yield Event("error", text=text)
