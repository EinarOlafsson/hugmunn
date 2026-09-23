"""Release gating must prevent reusing versions for different source commits."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

spec = importlib.util.spec_from_file_location(
    "hugmunn_release", Path(__file__).resolve().parents[1] / "packaging/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def plan_environment(monkeypatch, tmp_path):
    source = tmp_path / "src/hugmunn"
    source.mkdir(parents=True)
    (source / "_version.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 1.2.3\n\nFirst change.\n\n## 1.2.2\n\nOld change.\n")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "EinarOlafsson/hugmunn")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "tested-commit")
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return output


def fake_commands(monkeypatch, *, published=False, tag=None, api_error=None):
    def run(command, **kwargs):
        if command[0] == "gh":
            if published:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"draft": False}), stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr=api_error or "HTTP 404")
        return SimpleNamespace(returncode=0 if tag else 1, stdout=tag or "", stderr="")
    monkeypatch.setattr(release.subprocess, "run", run)


def test_a_new_version_requires_a_release(plan_environment, monkeypatch):
    fake_commands(monkeypatch)
    release.plan()
    assert "needed=true" in plan_environment.read_text()


def test_an_existing_release_is_not_republished(plan_environment, monkeypatch):
    fake_commands(monkeypatch, published=True, tag="older-commit")
    monkeypatch.setattr(release, "on_pypi", lambda value: True)
    release.plan()
    assert "needed=false" in plan_environment.read_text()


def test_a_github_release_can_retry_missing_pypi_publication(plan_environment, monkeypatch):
    fake_commands(monkeypatch, published=True, tag="tested-commit")
    monkeypatch.setattr(release, "on_pypi", lambda value: False)
    release.plan()
    assert "needed=true" in plan_environment.read_text()


@pytest.mark.parametrize("status,missing", [(404, True), (503, False)])
def test_pypi_outages_are_not_treated_as_missing_versions(monkeypatch, status, missing):
    def unavailable(*args, **kwargs):
        raise HTTPError("https://pypi.org", status, "failure", {}, None)
    monkeypatch.setattr(release, "urlopen", unavailable)
    if missing:
        assert release.on_pypi("1.2.3") is False
    else:
        with pytest.raises(HTTPError):
            release.on_pypi("1.2.3")


def test_an_interrupted_release_can_resume_at_the_same_commit(plan_environment, monkeypatch):
    fake_commands(monkeypatch, tag="tested-commit")
    release.plan()
    assert "needed=true" in plan_environment.read_text()


def test_a_reserved_version_cannot_publish_different_code(plan_environment, monkeypatch):
    fake_commands(monkeypatch, tag="different-commit")
    with pytest.raises(ValueError, match="bump the version"):
        release.plan()
    assert not plan_environment.exists()


def test_api_failure_is_not_mistaken_for_an_unreleased_version(plan_environment, monkeypatch):
    fake_commands(monkeypatch, api_error="HTTP 403: access denied")
    with pytest.raises(RuntimeError):
        release.plan()


def test_release_notes_include_only_the_selected_version(plan_environment):
    assert release.notes("1.2.3") == "First change.\n"
