"""Slash commands, remote access, and the persistence tier."""

from __future__ import annotations

import importlib
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from localagent.core import commands as commandkit
from localagent.core import persistence as persistkit
from localagent.core import remote as remotekit


# ------------------------------------------------------------- recognition


@pytest.mark.parametrize("text", ["/help", "/goal find the leak", "  /remote on"])
def test_a_slash_word_is_a_command(text):
    assert commandkit.is_command(text)


@pytest.mark.parametrize("text", [
    "/usr/local/bin/llama-server", "/2", "/", "", "not a command",
    "what does /goal do?",
])
def test_these_are_not_commands(text):
    """Pasting a path as the whole message is a normal thing to do."""
    assert not commandkit.is_command(text)


def test_parsing_splits_the_name_from_the_rest():
    assert commandkit.parse("/goal fix the flaky test") == ("goal", "fix the flaky test")
    assert commandkit.parse("/help") == ("help", "")


def test_an_unknown_command_suggests_the_nearest():
    message = commandkit.unknown("remot")
    assert "/remote" in message


def test_an_unrecognisable_command_still_explains_the_escape():
    message = commandkit.unknown("zzzzz")
    assert "/help" in message and "space before it" in message


def test_help_lists_every_command():
    text = commandkit.help_text()
    for command in commandkit.COMMANDS:
        assert command.usage in text


# ------------------------------------------------------------------- goal


def test_the_goal_block_carries_the_objective():
    block = commandkit.goal_block("make the tests pass")
    assert "make the tests pass" in block


def test_the_goal_block_forbids_the_failure_it_exists_to_prevent():
    block = commandkit.goal_block("x")
    assert "plan for how it might" in block            # not a plan
    assert "Met" in block and "Blocked" in block       # a stated outcome


# ------------------------------------------------------------ persistence


@pytest.mark.parametrize("level", list(persistkit.Persistence))
def test_every_tier_has_a_budget_a_label_and_an_explanation(level):
    assert persistkit.max_rounds(level) > 0
    assert persistkit.LABELS[level]
    assert len(persistkit.BLURBS[level]) > 40
    assert len(persistkit.BODIES[level]) > 60


def test_budgets_increase_with_the_tier():
    budgets = [persistkit.max_rounds(l) for l in persistkit.Persistence]
    assert budgets == sorted(budgets)


def test_the_lowest_tier_still_beats_the_old_default_at_the_top():
    """Twelve rounds was the reported problem; relentless must be far past it."""
    assert persistkit.max_rounds(persistkit.Persistence.RELENTLESS) >= 100


def test_a_longer_budget_gets_more_interruption_not_less():
    """A run permitted 200 rounds needs *more* stepping back, or it spends
    them all down one wrong assumption made in the first five."""
    assert (persistkit.recanvas_every(persistkit.Persistence.RELENTLESS)
            <= persistkit.recanvas_every(persistkit.Persistence.NORMAL))


def test_relentless_names_the_only_reasons_to_stop_early():
    body = persistkit.BODIES[persistkit.Persistence.RELENTLESS]
    assert "verified it rather than assumed" in body
    assert "decision that is the user's" in body
    assert "destructive" in body


def test_the_tier_reaches_the_agent():
    from localagent.core.agent import Agent

    for level in persistkit.Persistence:
        agent = Agent(client=None, workdir="/tmp", system_prompt="",
                      persistence=level)
        assert agent.max_iterations == persistkit.max_rounds(level)
        assert agent.recanvas_every == persistkit.recanvas_every(level)
        assert persistkit.LABELS[level] in agent.system_prompt


def test_an_explicit_budget_still_wins_for_subagents():
    from localagent.core.agent import Agent

    assert Agent(client=None, workdir="/tmp", system_prompt="",
                 max_iterations=3).max_iterations == 3


# ----------------------------------------------------------------- remote


def test_a_token_is_long_and_unguessable():
    token = remotekit.new_token()
    assert len(token) >= 40
    assert token != remotekit.new_token()


def test_token_comparison_rejects_the_obvious_wrongs():
    token = remotekit.new_token()
    assert remotekit.token_matches(token, token)
    assert not remotekit.token_matches("", token)
    assert not remotekit.token_matches(token[:-1], token)
    assert not remotekit.token_matches(token, "")


def test_repeated_failures_lock_an_address_out():
    """A found port must not be a free guessing oracle."""
    limiter = remotekit.RateLimiter(max_failures=3, window=60, lockout=60)
    for _ in range(3):
        limiter.record_failure("10.0.0.9")
    assert limiter.is_locked("10.0.0.9")
    assert not limiter.is_locked("10.0.0.10")


def test_a_success_clears_the_count():
    limiter = remotekit.RateLimiter(max_failures=3)
    limiter.record_failure("a")
    limiter.record_success("a")
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert not limiter.is_locked("a")


def test_loopback_says_nothing_is_exposed_yet():
    assert "only from this machine" in remotekit.Exposure("127.0.0.1", 8770).warning()


def test_binding_to_every_interface_says_so_plainly():
    warning = remotekit.Exposure("0.0.0.0", 8770).warning()
    assert "every network" in warning and "run commands" in warning


def test_tunnels_are_offered_rather_than_implemented():
    """localagent should not be running a tunnel daemon; these do it better."""
    options = remotekit.tunnel_options(8770)
    names = {o["name"] for o in options}
    assert "Tailscale" in names and "Cloudflare Tunnel" in names
    assert all("8770" in o["command"] for o in options)
    # The safer option must say why it is safer.
    tailscale = next(o for o in options if o["name"] == "Tailscale")
    assert "nothing is published" in tailscale["note"].lower()


# ------------------------------------------------------- the server itself


class StubBridge:
    def __init__(self):
        self.sent = []
        self.approved = []

    def snapshot(self):
        return {"model": "test", "messages": [], "pending_approvals": []}

    def send(self, text):
        self.sent.append(text)
        yield {"kind": "content", "text": "hello " + text}

    def resolve_approval(self, call_id, allowed):
        self.approved.append((call_id, allowed))
        return True

    def apply_setting(self, key, value):
        return {"set": key}

    def cancel(self):
        pass


@pytest.fixture()
def server():
    from localagent.core.webserver import RemoteServer

    bridge = StubBridge()
    instance = RemoteServer(bridge, host="127.0.0.1", port=0)
    instance.start()
    # Port 0 asks the OS to choose; read back what it gave us.
    instance.port = instance._server.server_address[1]
    yield instance, bridge
    instance.stop()


def fetch(server, path, token=None, body=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST" if body is not None else "GET",
    )
    return urllib.request.urlopen(request, timeout=10)


def test_the_server_serves_a_page_with_the_token(server):
    instance, _ = server
    body = fetch(instance, "/", instance.token).read().decode()
    assert "<html" in body and "localagent" in body


def test_no_token_is_refused(server):
    instance, _ = server
    with pytest.raises(urllib.error.HTTPError) as caught:
        fetch(instance, "/api/state")
    assert caught.value.code == 401


def test_a_wrong_token_is_refused(server):
    instance, _ = server
    with pytest.raises(urllib.error.HTTPError) as caught:
        fetch(instance, "/api/state", "not-the-token")
    assert caught.value.code == 401


def test_the_error_does_not_say_which_kind_of_wrong(server):
    """Telling a missing token from a wrong one tells an attacker they are close."""
    instance, _ = server
    missing = wrong = None
    try:
        fetch(instance, "/api/state")
    except urllib.error.HTTPError as exc:
        missing = exc.read()
    try:
        fetch(instance, "/api/state", "x" * 43)
    except urllib.error.HTTPError as exc:
        wrong = exc.read()
    assert missing == wrong


def test_health_needs_no_token_and_leaks_nothing(server):
    """So a tunnel can be tested without handing over the key."""
    instance, _ = server
    body = json.loads(fetch(instance, "/health").read())
    assert body["ok"] is True
    assert "token" not in json.dumps(body).lower()


def test_the_page_is_not_framable_or_cacheable(server):
    instance, _ = server
    headers = fetch(instance, "/", instance.token).headers
    assert headers["X-Frame-Options"] == "DENY"
    assert "no-store" in headers["Cache-Control"]
    assert headers["Referrer-Policy"] == "no-referrer"


def test_sending_reaches_the_bridge_and_streams_back(server):
    instance, bridge = server
    body = fetch(instance, "/api/send", instance.token, {"text": "hi"}).read().decode()
    assert bridge.sent == ["hi"]
    assert "hello hi" in body


def test_an_approval_answer_reaches_the_bridge(server):
    instance, bridge = server
    fetch(instance, "/api/approve", instance.token, {"id": "c1", "allowed": True})
    assert bridge.approved == [("c1", True)]


def test_rotating_the_token_invalidates_the_old_one(server):
    instance, _ = server
    old = instance.token
    instance.rotate_token()
    with pytest.raises(urllib.error.HTTPError):
        fetch(instance, "/api/state", old)
    assert fetch(instance, "/api/state", instance.token).status == 200


def test_the_url_carries_the_token_and_the_page_strips_it():
    from localagent.core.webserver import RemoteServer
    from localagent.core.webui import PAGE

    instance = RemoteServer(StubBridge(), port=8770, token="abc")
    assert "?t=abc" in instance.url()
    # The page must not leave it in history or send it as a referrer.
    assert "history.replaceState" in PAGE
    assert 'name="referrer" content="no-referrer"' in PAGE


def test_the_server_binds_loopback_by_default():
    from localagent.core.webserver import RemoteServer

    assert RemoteServer(StubBridge()).host == "127.0.0.1"
