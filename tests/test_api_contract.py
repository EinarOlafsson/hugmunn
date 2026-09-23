"""Public API errors, configuration, and persistence regressions."""

import json
from unittest.mock import Mock

import pytest

import hugmunn
from hugmunn import api, config


@pytest.fixture
def client(monkeypatch):
    fake = Mock()
    monkeypatch.setattr(hugmunn.Agent, "_client", fake)
    return fake


@pytest.mark.parametrize("options", [
    {"effort": 0}, {"persistence": 5}, {"autonomy": -1},
    {"tools": "read_file"}, {"tools": ["missing-tool"]},
    {"skills": "python-quality"}, {"skills": ["missing-skill"]},
])
def test_invalid_agent_options_never_start_a_client(client, options):
    with pytest.raises(ValueError):
        hugmunn.Agent("code-glm", **options)
    client.assert_not_called()


def test_all_public_application_errors_share_the_base():
    for error in (hugmunn.ModelNotFound, hugmunn.ApprovalRequired,
                  hugmunn.ProviderError, hugmunn.ServerError):
        assert issubclass(error, hugmunn.HugmunnError)


def test_api_module_and_package_export_the_same_names():
    assert set(api.__all__) == set(hugmunn.__all__)


def test_discovery_restores_paths_selected_in_the_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    weights = tmp_path / "chosen-model.gguf"
    weights.write_bytes(b"gguf")
    (tmp_path / "settings.json").write_text(
        '{"model_paths": {"code-glm": ' + json.dumps(str(weights)) + '}}')
    config._MODEL_PATHS.clear()
    hugmunn.models("local")
    assert config.model_path_override("code-glm") == weights


def test_library_saves_are_closed_and_replace_the_requested_file(client, tmp_path):
    import json

    path = tmp_path / "chat.json"
    path.write_text("old contents")
    with hugmunn.agent("code-glm") as chat:
        chat.history = [{"role": "user", "content": "hello"}]
        assert chat.save(str(path)) == str(path)
    saved = json.loads(path.read_text())
    assert saved["closed_cleanly"] is True
    assert saved["messages"][0]["content"] == "hello"
    assert not list(tmp_path.glob("*.tmp"))


def test_default_save_reports_an_unwritable_destination(client, monkeypatch, tmp_path):
    config_path = tmp_path / "not-a-directory"
    config_path.write_text("occupied")
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(config_path))
    with hugmunn.agent("code-glm") as chat:
        with pytest.raises(OSError):
            chat.save()


def test_negative_session_limit_is_rejected():
    with pytest.raises(ValueError):
        hugmunn.sessions(-1)


def test_cloud_client_receives_requested_effort(monkeypatch):
    from hugmunn.core import cli

    monkeypatch.setattr(cli, "status", lambda *a, **kw: cli.Status(True, True, "Connected"))
    build = Mock(return_value=Mock())
    monkeypatch.setattr(cli, "CliClient", build)
    key = hugmunn.models("claude")[0].key
    with hugmunn.agent(key, effort=hugmunn.Effort.EXHAUSTIVE):
        pass
    assert build.call_args.kwargs["effort_level"] == 4


@pytest.mark.parametrize("contents", ['[]', 'null', '42', '{"messages": "not a list"}'])
def test_loading_invalid_session_structure_raises_a_public_error(client, tmp_path, contents):
    path = tmp_path / "invalid.json"
    path.write_text(contents)
    with hugmunn.agent("code-glm") as chat:
        with pytest.raises(hugmunn.HugmunnError):
            chat.load(str(path))
