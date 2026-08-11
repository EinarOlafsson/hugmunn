"""System prompt presets, and per-request control of thinking.

Both came out of the same finding: a model that refused with thinking on and
complied with it off. The mechanism is in the chat template — Qwen3 defines
``enable_thinking`` and defaults it to *true*, so a model left alone thinks
before every answer, and the refusal forms there.
"""

from __future__ import annotations

import json

import httpx
import pytest

from localagent.core import prompts as promptkit
from localagent.core.client import LlamaClient


# ------------------------------------------------------------ the presets


def test_every_preset_has_a_label_and_an_explanation():
    for preset in promptkit.PRESETS:
        assert preset.label and len(preset.note) > 20


def test_there_is_a_preset_with_no_prompt_at_all():
    """The least-shaped the model can be; nothing added it did not ask for."""
    assert promptkit.BY_KEY["none"].text == ""


def test_the_minimal_preset_adds_no_persona():
    """Assistant framing is part of what brings refusals back."""
    text = promptkit.BY_KEY["minimal"].text.lower()
    assert "you are a" not in text
    assert "assistant" not in text


def test_the_research_preset_says_clinical_material_is_normal():
    text = promptkit.BY_KEY["research"].text.lower()
    assert "scientific" in text or "clinical" in text
    assert "refus" in text          # names the failure it exists to prevent


def test_a_preset_is_recognised_when_it_is_in_use():
    assert promptkit.match(promptkit.BY_KEY["coding"].text) == "coding"
    assert promptkit.match(promptkit.BY_KEY["none"].text) == "none"


def test_an_edited_prompt_reads_as_custom():
    assert promptkit.match("something I typed myself") == "custom"


def test_whitespace_does_not_break_recognition():
    assert promptkit.match("  " + promptkit.DEFAULT + "\n") == "default"


def test_the_default_setting_is_one_of_the_presets():
    from localagent.config import Settings

    assert promptkit.match(Settings().system_prompt) != "custom"


# ---------------------------------------------------- thinking, per request


class _Capture:
    """Records the JSON body instead of sending it."""

    def __init__(self):
        self.payload = {}

    def __call__(self, *args, **kwargs):
        self.payload = kwargs.get("json") or {}
        return self

    def __enter__(self):
        class Response:
            status_code = 500

            def read(self_inner):
                return b"{}"
        return Response()

    def __exit__(self, *args):
        return False


@pytest.fixture()
def captured(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(httpx.Client, "stream", lambda self, *a, **k: capture(*a, **k))
    return capture


def send(captured, **kwargs):
    list(LlamaClient("http://x").stream([{"role": "user", "content": "hi"}], **kwargs))
    return captured.payload


def test_thinking_off_is_sent_as_a_template_argument(captured):
    payload = send(captured, thinking=False)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_on_is_sent_explicitly_too(captured):
    """Not just omitted -- a launch flag may have turned it off server-side."""
    payload = send(captured, thinking=True)
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}


def test_saying_nothing_sends_nothing(captured):
    """A model whose template has no such variable must not be handed one."""
    assert "chat_template_kwargs" not in send(captured)


def test_the_agent_passes_thinking_through(captured):
    from localagent.core.agent import Agent

    agent = Agent(client=LlamaClient("http://x"), workdir="/tmp",
                  system_prompt="", use_tools=False, thinking=False)
    list(agent.run([{"role": "user", "content": "hi"}], approve=lambda *a: True))
    assert captured.payload.get("chat_template_kwargs") == {"enable_thinking": False}


def test_the_cloud_clients_accept_the_argument_without_choking():
    """Their thinking is the effort tier; the agent still passes the keyword."""
    import inspect

    from localagent.core.cloud import AnthropicClient, OpenAIClient

    for client in (AnthropicClient, OpenAIClient):
        assert "thinking" in inspect.signature(client.stream).parameters


def test_the_uncensored_models_default_to_thinking_off():
    """What the user found the hard way, recorded where it takes effect."""
    from localagent import config

    for key in ("uncensored", "uncensored-big"):
        assert config.by_key(key).reasoning == "off"


def test_the_uncensored_blurbs_say_why():
    from localagent import config

    for key in ("uncensored", "uncensored-big"):
        assert "refusal" in config.by_key(key).blurb.lower()
