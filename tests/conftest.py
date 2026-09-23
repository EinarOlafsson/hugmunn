"""Shared fixtures, and undoing the state a reloaded module leaves behind."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


#: Held for the life of the process. Python may collect the QApplication
#: before the widgets that reference it, and destroying a widget whose
#: application is already gone segfaults during interpreter shutdown -- after
#: the tests have reported success, which is a confusing place to find it.
_APPLICATION = None


@pytest.fixture
def model_scripts(tmp_path, monkeypatch):
    """Provide launch scripts without depending on a developer's model folder."""
    from hugmunn import config

    root = tmp_path / "model-fixtures"
    monkeypatch.setenv("HUGMUNN_MODELS_ROOT", str(root))
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path / "configuration"))
    (root / "scripts").mkdir(parents=True)
    for spec in config.REGISTRY:
        if not spec.files:
            continue
        folder = "qwen3.5-122b" if spec.key == "write-big" else spec.key
        script = spec.script_path
        script.write_text(
            '#!/bin/sh\nexec llama-server \\\n'
            f'  --model "$BASE/gguf/{folder}/{Path(spec.files[0]).name}" \\\n'
            f'  --ctx-size {spec.ctx_size} \\\n'
            '  "$@"\n', encoding="utf-8",
        )
        script.chmod(0o755)
    return root


@pytest.fixture
def model_runtime(model_scripts, monkeypatch):
    """Provide tiny weight files and a stub executable for argument-only tests."""
    from hugmunn import config

    runtime = model_scripts / "llama-server"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    monkeypatch.setenv("LLAMA_SERVER", str(runtime))
    config.set_runtime(None)
    for spec in config.REGISTRY:
        for path in spec.expected_files().values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test weights")
    return runtime


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole session; Qt allows only one."""
    global _APPLICATION
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    if _APPLICATION is None:
        _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


@pytest.fixture(autouse=True)
def no_leftover_widgets():
    """Actually destroy the widgets a test built.

    ``close()`` hides a window; it does not delete it. The QApplication is
    session-scoped, so without this every window ever built stays alive for
    the whole run -- and ``app.setStyleSheet``, which several tests call,
    walks every widget Qt knows about. By the third GUI file that is hundreds
    of stale windows, one of which is mid-teardown, and it segfaults.

    Deleting them is not optional tidiness: the suite passed file by file and
    crashed in combination, which is the most confusing shape a bug can have.
    """
    yield destroy_widgets
    destroy_widgets()


def destroy_widgets():
    """Deliver deferred widget deletions before changing global Qt styles."""
    try:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        for widget in list(app.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        app.processEvents()
        # deleteLater only *posts* the delete; this is what delivers it.
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def pristine_config():
    """Put ``hugmunn.config`` back the way it was found.

    Several tests reload it against a temporary models root, because
    MODELS_ROOT is resolved at import. ``monkeypatch`` restores the
    environment variable but not the module that already read it, so without
    this the *next* test sees a models root inside somebody else's tmp_path —
    which showed up as two unrelated failures and sixteen silent skips.
    """
    from hugmunn import config

    before = (config.MODELS_ROOT, config.CONFIG_DIR)
    yield
    if (config.MODELS_ROOT, config.CONFIG_DIR) != before:
        importlib.reload(config)
    # Module-level overrides outlive a reload only if the module object is the
    # same one, so clear them either way.
    config._MODEL_PATHS.clear()
    config._CONTEXT_SIZES.clear()
    config.set_runtime(None)
