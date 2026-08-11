"""Registry and shard-completeness tests.

The shard check exists because a half-downloaded 100 GB model otherwise reports
itself ready and fails at load time with an opaque llama.cpp error.
"""

from __future__ import annotations

import json

import pytest

from localagent import config


def touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


class TestWeightsComplete:
    def test_single_file_present(self, tmp_path):
        assert config._weights_complete(touch(tmp_path / "model.gguf"))

    def test_single_file_absent(self, tmp_path):
        assert not config._weights_complete(tmp_path / "missing.gguf")

    def test_all_shards_present(self, tmp_path):
        for n in (1, 2, 3):
            touch(tmp_path / f"m-{n:05d}-of-00003.gguf")
        assert config._weights_complete(tmp_path / "m-00001-of-00003.gguf")

    def test_middle_shard_missing(self, tmp_path):
        # Exactly the MiniMax case: first and last arrive before the big middle ones.
        touch(tmp_path / "m-00001-of-00004.gguf")
        touch(tmp_path / "m-00004-of-00004.gguf")
        assert not config._weights_complete(tmp_path / "m-00001-of-00004.gguf")

    def test_last_shard_missing(self, tmp_path):
        touch(tmp_path / "m-00001-of-00002.gguf")
        assert not config._weights_complete(tmp_path / "m-00001-of-00002.gguf")


class TestModelSpec:
    def _spec(self, tmp_path, monkeypatch, model_line):
        monkeypatch.setattr(config, "MODELS_ROOT", tmp_path)
        script = tmp_path / "scripts" / "fake.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "#!/usr/bin/env bash\n"
            'exec "$BASE/bin/llama-server" \\\n'
            f"  {model_line} \\\n"
            "  --port 9999\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return config.ModelSpec("k", "Fake", "fake.sh", 9999, "blurb")

    def test_available_when_weights_present(self, tmp_path, monkeypatch):
        spec = self._spec(tmp_path, monkeypatch, '--model "$BASE/gguf/m.gguf"')
        touch(tmp_path / "gguf" / "m.gguf")
        assert spec.is_available()

    def test_unavailable_when_weights_missing(self, tmp_path, monkeypatch):
        spec = self._spec(tmp_path, monkeypatch, '--model "$BASE/gguf/m.gguf"')
        assert not spec.is_available()

    def test_unavailable_when_shards_incomplete(self, tmp_path, monkeypatch):
        spec = self._spec(tmp_path, monkeypatch, '--model "$BASE/gguf/d/m-00001-of-00003.gguf"')
        touch(tmp_path / "gguf" / "d" / "m-00001-of-00003.gguf")
        assert not spec.is_available()

    def test_unavailable_when_script_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "MODELS_ROOT", tmp_path)
        assert not config.ModelSpec("k", "L", "nope.sh", 1, "b").is_available()

    def test_base_url_uses_port(self):
        assert config.ModelSpec("k", "L", "s.sh", 8084, "b").base_url == "http://127.0.0.1:8084"


class TestRegistry:
    def test_keys_unique(self):
        keys = [m.key for m in config.REGISTRY]
        assert len(keys) == len(set(keys))

    def test_ports_unique(self):
        ports = [m.port for m in config.REGISTRY]
        assert len(ports) == len(set(ports))

    def test_by_key_roundtrip(self):
        for spec in config.REGISTRY:
            assert config.by_key(spec.key) is spec

    def test_by_key_unknown(self):
        assert config.by_key("nonexistent") is None

    def test_every_shipped_model_has_tools(self):
        """Including the abliterated ones — both measured 3/3 structured calls.

        The tools_reliable flag stays as an escape hatch, but nothing shipped
        sets it False. If a future model does, test that build first.
        """
        assert all(m.tools_reliable for m in config.REGISTRY)


class TestSettings:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
        original = config.Settings(model_key="write", workdir="/tmp", tools_enabled=False)
        original.save()
        assert config.Settings.load().model_key == "write"
        assert config.Settings.load().tools_enabled is False

    def test_missing_file_gives_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.json")
        assert config.Settings.load().model_key == config.Settings().model_key

    def test_corrupt_file_gives_defaults(self, tmp_path, monkeypatch):
        bad = tmp_path / "settings.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_FILE", bad)
        assert config.Settings.load().model_key == config.Settings().model_key

    def test_unknown_keys_ignored(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"model_key": "code", "removed_option": 1}), encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_FILE", path)
        assert config.Settings.load().model_key == "code"

    def test_save_is_atomic(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
        config.Settings().save()
        assert not list(tmp_path.glob("*.tmp"))  # temp file renamed away


class TestVersion:
    def test_package_and_pyproject_agree(self):
        """A version bumped in one place and not the other ships a lie."""
        import re
        from pathlib import Path

        import localagent

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
        assert match, "no version in pyproject.toml"
        assert match.group(1) == localagent.__version__

    def test_changelog_documents_the_current_version(self):
        from pathlib import Path

        import localagent

        changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
        assert f"## {localagent.__version__}" in changelog.read_text()


class TestContextSize:
    """The UI needs the real --ctx-size to warn before a 400 happens."""

    def test_parsed_from_the_launch_script(self):
        assert config.by_key("code-glm").context_tokens == 32768
        assert config.by_key("write").context_tokens == 16384

    def test_every_shipped_model_declares_one(self):
        missing = [m.key for m in config.REGISTRY if not m.context_tokens]
        assert not missing, f"no --ctx-size found: {missing}"

    def test_missing_script_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "MODELS_ROOT", tmp_path)
        assert config.ModelSpec("k", "L", "nope.sh", 1, "b").context_tokens == 0
