"""CLI subscription transport: no real provider calls or credentials in tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from hugmunn.core import cli, providers
from hugmunn.core.providers import CloudModel, Provider


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_STATUS", {})
    monkeypatch.setattr(providers, "_LIVE", {})


@pytest.mark.parametrize("provider", providers.CLOUD)
def test_response_and_tool_proposals_return_to_hugmunn(provider, monkeypatch):
    monkeypatch.setattr(cli, "status", lambda *a, **kw: cli.Status(True, True, "Connected"))
    monkeypatch.setattr(cli, "executable", lambda p: cli.NAMES[p])
    payload = {"answer": "I will read the file.", "tool_calls": [{"name": "read_file", "arguments_json": '{"path":"README.md"}'}]}
    seen = {}
    def run(argv, prompt, folder, cancel, timeout):
        seen.update(argv=argv, prompt=json.loads(prompt.split('\n', 1)[1]), folder=folder)
        (folder / 'answer.json').write_text(json.dumps(payload))
        return 0, json.dumps({"structured_output": payload}), ""
    monkeypatch.setattr(cli, "_run", run)
    events = list(cli.CliClient(CloudModel('default', 'Default', provider), 4).stream(
        [{'role': 'user', 'content': 'Read README.md'}],
        [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}]))
    assert [e.kind for e in events] == ['content', 'tool_calls', 'done']
    assert events[1].tool_calls[0].parsed_arguments() == {'path': 'README.md'}
    assert not seen['folder'].exists()
    assert '--model' not in seen['argv']
    assert '--dangerously-bypass-approvals-and-sandbox' not in seen['argv']
    if provider == Provider.ANTHROPIC:
        assert seen['argv'][seen['argv'].index('--tools') + 1] == ''
        assert seen['argv'][seen['argv'].index('--effort') + 1] == 'xhigh'
    else:
        assert seen['argv'][seen['argv'].index('--sandbox') + 1] == 'read-only'
        assert '--ignore-user-config' in seen['argv']


@pytest.mark.parametrize('payload', [
    {'answer': 'bad', 'tool_calls': [{'name':'shell', 'arguments_json':'{}'}]},
    {'answer': 'bad', 'tool_calls': [{'name':'read_file', 'arguments_json':'[]'}]},
    {'answer': 'bad', 'tool_calls': 'invalid'},
    {'answer': 42, 'tool_calls': []},
])
def test_invalid_or_unadvertised_tools_never_execute(payload, monkeypatch):
    monkeypatch.setattr(cli, 'status', lambda *a, **k: cli.Status(True, True, ''))
    monkeypatch.setattr(cli, 'executable', lambda p: 'claude')
    monkeypatch.setattr(cli, '_run', lambda *a: (0, json.dumps({'structured_output':payload}), ''))
    events = list(cli.CliClient(CloudModel('default','',Provider.ANTHROPIC)).stream([], tools=[]))
    assert [e.kind for e in events] == ['error']


def test_api_environment_is_not_passed_to_children(monkeypatch):
    for key in ('OPENAI_API_KEY','CODEX_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN'):
        monkeypatch.setenv(key, 'secret')
    assert all('secret' != value for value in cli.environment().values())
    assert cli.environment()['CODEX_HOME'] == os.environ['CODEX_HOME']


@pytest.mark.parametrize('provider,stdout,stderr,expected', [
    (Provider.ANTHROPIC, '{"loggedIn":true,"authMethod":"claude.ai"}', '', True),
    (Provider.ANTHROPIC, '{"loggedIn":true,"authMethod":"api_key"}', '', False),
    (Provider.OPENAI, '', 'Logged in using ChatGPT', True),
    (Provider.OPENAI, '', 'Logged in using an API key', False),
])
def test_status_only_accepts_subscription_login(provider, stdout, stderr, expected, monkeypatch):
    monkeypatch.delenv('HUGMUNN_PYTEST_SESSION')
    monkeypatch.setattr(cli, 'executable', lambda p: 'fake-cli')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a,0,stdout,stderr))
    assert cli.status(provider, refresh=True).signed_in is expected


def test_model_cache_omits_hidden_models_and_keeps_default(tmp_path):
    (tmp_path / 'models_cache.json').write_text(json.dumps({'models':[
        {'slug':'visible','display_name':'Visible','visibility':'list','context_window':1234},
        {'slug':'hidden','visibility':'hide'}]}))
    models = cli.refresh_models(Provider.OPENAI)
    assert [m.id for m in models] == ['default','visible']
    assert models[1].context == 1234


def test_silent_process_is_cancelled_and_reaped(tmp_path, monkeypatch):
    # A local Python fixture only; never invoke either installed provider CLI.
    monkeypatch.delenv('HUGMUNN_PYTEST_SESSION')
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    start = time.monotonic()
    result = cli._run([sys.executable,'-c','import time; time.sleep(60)'], '', tmp_path, cancel, 10)
    timer.join()
    assert result == (-1, '', 'cancelled')
    assert time.monotonic() - start < 6


def test_stdin_and_nonzero_exit_are_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv('HUGMUNN_PYTEST_SESSION')
    result = cli._run([sys.executable,'-c','import sys; print(sys.stdin.read()); sys.exit(3)'],
                      'hello $(not a command)', tmp_path, None, 10)
    assert result[0] == 3
    assert result[1].strip() == 'hello $(not a command)'


def test_public_api_rejects_keys_and_accepts_codex_alias(monkeypatch):
    import hugmunn
    with pytest.raises(ValueError, match='subscription'):
        hugmunn.sign_in('codex', 'not-a-real-key')
    monkeypatch.setattr(cli, 'status', lambda *a, **kw: cli.Status(True, True, ''))
    assert hugmunn.sign_in('codex') >= 1
    assert hugmunn.models('codex')[0].key == 'chatgpt:default'


def test_frozen_launcher_restores_external_library_search(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/temporary/bundle')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', '/user/libraries')
    monkeypatch.setenv('DYLD_LIBRARY_PATH', '/temporary/bundle')
    monkeypatch.delenv('DYLD_LIBRARY_PATH_ORIG', raising=False)
    env = cli.environment()
    assert env['LD_LIBRARY_PATH'] == '/user/libraries'
    assert 'DYLD_LIBRARY_PATH' not in env
