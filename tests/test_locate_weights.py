"""Pointing the app at weights you already have.

The reported failure: the download dialog opens for whichever model is
selected -- on a fresh install the *smallest*, Qwen3.6-27B coding at 17.6 GB
-- and the user pointed it at their uncensored 27B. The app answered that a
sibling shard was missing, naming a file from a completely different model,
about a model that ships as a single file. Both wrong and unactionable.
"""

from __future__ import annotations

import importlib

import pytest

from localagent import config


# ---------------------------------------------------------- identification


def test_the_reported_file_is_recognised_as_the_model_it_actually_is():
    spec = config.identify(
        "Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf")
    assert spec is not None
    assert spec.key == "uncensored"


def test_the_file_named_in_the_error_belongs_to_a_different_model():
    """Which is why the old message made no sense: two unrelated models."""
    mine = config.identify("Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf")
    theirs = config.identify("Qwen3.6-27B-UD-Q4_K_XL.gguf")
    assert mine.key == "uncensored"
    assert theirs.key == "code"
    assert mine.key != theirs.key


def test_a_full_path_identifies_the_same_as_a_bare_name():
    a = config.identify("/somewhere/else/Qwen3.6-27B-UD-Q5_K_XL.gguf")
    b = config.identify("Qwen3.6-27B-UD-Q5_K_XL.gguf")
    assert a is not None and a.key == b.key == "write"


def test_an_unrelated_gguf_identifies_as_nothing():
    assert config.identify("some-other-model-Q4.gguf") is None


def test_every_registered_model_identifies_its_own_first_file():
    for spec in config.REGISTRY:
        if not spec.files:
            continue
        found = config.identify(spec.files[0])
        assert found is not None and found.key == spec.key, spec.key


def test_no_two_models_claim_the_same_filename():
    """Identification is by name, so a collision would silently misfile."""
    seen: dict[str, str] = {}
    for spec in config.REGISTRY:
        for name in spec.files:
            from pathlib import Path

            leaf = Path(name).name
            assert leaf not in seen, f"{leaf} claimed by {seen.get(leaf)} and {spec.key}"
            seen[leaf] = spec.key


# ------------------------------------------------------------- folder scan


@pytest.fixture()
def weights_folder(tmp_path):
    """A folder holding exactly the two models the user copied across."""
    (tmp_path / "Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf"
     ).write_bytes(b"x")
    (tmp_path / "Qwen3.5-122B-A10B-abliterated.i1-Q5_K_M.gguf").write_bytes(b"x")
    return tmp_path


def test_scanning_a_folder_finds_both_models_at_once(weights_folder):
    found = config.scan_for_weights(weights_folder)
    assert set(found) == {"uncensored", "uncensored-big"}


def test_scanning_reaches_into_subdirectories(tmp_path):
    nested = tmp_path / "gguf" / "coder-next-q6" / "UD-Q6_K"
    nested.mkdir(parents=True)
    for n in (1, 2, 3):
        (nested / f"Qwen3-Coder-Next-UD-Q6_K-{n:05d}-of-00003.gguf").write_bytes(b"x")
    assert "code-q6" in config.scan_for_weights(tmp_path)


def test_an_incomplete_shard_set_is_not_reported_as_found(tmp_path):
    """Two of three loads with an opaque llama.cpp error, so it is not 'found'."""
    nested = tmp_path / "UD-Q6_K"
    nested.mkdir()
    for n in (1, 2):
        (nested / f"Qwen3-Coder-Next-UD-Q6_K-{n:05d}-of-00003.gguf").write_bytes(b"x")
    assert config.scan_for_weights(tmp_path) == {}


def test_scanning_an_empty_or_missing_folder_is_not_an_error(tmp_path):
    assert config.scan_for_weights(tmp_path) == {}
    assert config.scan_for_weights(tmp_path / "nope") == {}


def test_scanning_does_not_walk_without_bound(tmp_path):
    """These folders sit on network mounts holding hundreds of gigabytes."""
    deep = tmp_path
    for level in range(8):
        deep = deep / f"level{level}"
    deep.mkdir(parents=True)
    (deep / "Qwen3.6-27B-UD-Q5_K_XL.gguf").write_bytes(b"x")
    assert config.scan_for_weights(tmp_path, max_depth=3) == {}


# ------------------------------------------------------ the dialog itself


def test_the_dialog_records_a_file_belonging_to_another_model(qt_app, weights_folder,
                                                              monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from localagent.ui.download_dialog import DownloadDialog

    target = weights_folder / (
        "Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    # Opened for the *coding* model, which is what a fresh install selects.
    dialog = DownloadDialog(config.by_key("code"))
    dialog._locate_existing()
    # Filed against the model it actually is, not the one the dialog was for.
    assert dialog.located() == {"uncensored": target}
    dialog.close()


def test_the_dialog_refuses_a_file_it_does_not_recognise(qt_app, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from localagent.ui.download_dialog import DownloadDialog

    stray = tmp_path / "some-unknown-model-Q8.gguf"
    stray.write_bytes(b"x")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(stray), "")))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a)))

    dialog = DownloadDialog(config.by_key("code"))
    dialog._locate_existing()
    assert dialog.located() == {}
    assert warned, "an unrecognised file must say so"
    # And it must not claim a shard is missing, which is what it used to do.
    assert "shard" not in " ".join(str(a) for a in warned[0]).lower()
    dialog.close()


def test_scanning_from_the_dialog_records_every_model_found(qt_app, weights_folder,
                                                            monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from localagent.ui.download_dialog import DownloadDialog

    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(weights_folder)))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    dialog = DownloadDialog(config.by_key("code"))
    dialog._scan_folder()
    assert set(dialog.located()) == {"uncensored", "uncensored-big"}
    dialog.close()


def test_find_my_models_is_reachable_from_the_menu(qt_app, weights_folder, tmp_path,
                                                   monkeypatch):
    """Without having to open a download dialog for the wrong model first."""
    import importlib

    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from localagent import config as cfg

    importlib.reload(cfg)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, spec: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, provider: None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(weights_folder)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window = mw.MainWindow()
    try:
        entries = [a.text() for a in window.menuBar().actions()[0].menu().actions()]
        assert any("Find my models" in e for e in entries)
        window._find_models()
        assert cfg.model_path_override("uncensored") is not None
        assert cfg.model_path_override("uncensored-big") is not None
    finally:
        window.server.stop()
        window.close()
