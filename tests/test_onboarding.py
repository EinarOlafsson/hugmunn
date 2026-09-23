"""Setup acceptance, report privacy and account isolation, without live services."""

import json

import pytest

from hugmunn import config
from hugmunn.core import onboarding, reporting


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_INFERENCE_BACKEND", "auto")
    return tmp_path


def test_agreement_is_versioned_and_persisted(isolated):
    settings = config.Settings()
    assert onboarding.needs_setup(settings)
    assert settings.automatic_reports is True
    onboarding.accept_agreement(settings)
    assert not onboarding.needs_setup(config.Settings.load())
    settings.agreement_version = "older"
    assert onboarding.needs_setup(settings)


def test_reports_exclude_error_messages_and_custom_exception_names():
    secret = "sk-secret private conversation /home/alice/patient-data.csv"
    custom = type("PrivatePatientName", (Exception,), {})
    try:
        raise custom(secret)
    except Exception as exc:
        payload = reporting.make_report("untrusted category " + secret, exc)
    text = json.dumps(payload)
    assert secret not in text and "PrivatePatientName" not in text
    assert "Exception: Exception" in text and "Category: unexpected" in text
    assert "test_onboarding" not in text


@pytest.mark.parametrize("accepted,enabled,account", [(False, True, "alice"), (True, False, "alice"), (True, True, "")])
def test_reports_require_agreement_preference_and_account(isolated, monkeypatch, accepted, enabled, account):
    s = config.Settings(automatic_reports=enabled, github_account=account)
    onboarding.accept_agreement(s) if accepted else s.save()
    monkeypatch.setattr(reporting, "_gh_api", lambda *a: pytest.fail("must not contact GitHub"))
    assert reporting.send_report(reporting.make_report("model-request")) == ""


def test_account_switch_does_not_silently_change_author(isolated, monkeypatch):
    onboarding.accept_agreement(config.Settings(github_account="alice"))
    monkeypatch.setattr(reporting, "github_account", lambda: "bob")
    monkeypatch.setattr(reporting, "_gh_api", lambda *a: pytest.fail("must not post"))
    assert reporting.send_report(reporting.make_report("unexpected")) == ""


def test_reporting_deduplicates_and_limits_public_issues(isolated, monkeypatch):
    onboarding.accept_agreement(config.Settings(github_account="alice"))
    monkeypatch.setattr(reporting, "github_account", lambda: "alice")
    sent = []
    def api(endpoint, payload=None):
        assert endpoint.startswith("repos/EinarOlafsson/hugmunn/issues")
        if payload is None:
            return []
        sent.append(payload)
        return {"html_url": "https://github.com/EinarOlafsson/hugmunn/issues/1"}
    monkeypatch.setattr(reporting, "_gh_api", api)
    one = reporting.make_report("model-request")
    assert reporting.send_report(one).endswith("/1")
    assert reporting.send_report(one) == ""
    reporting.send_report(reporting.make_report("model-start"))
    reporting.send_report(reporting.make_report("model-download"))
    assert reporting.send_report(reporting.make_report("unexpected")) == ""
    assert len(sent) == 3


def test_opt_out_during_authentication_prevents_post(isolated, monkeypatch):
    s = config.Settings(github_account="alice")
    onboarding.accept_agreement(s)
    def account():
        s.automatic_reports = False
        s.save()
        return "alice"
    monkeypatch.setattr(reporting, "github_account", account)
    monkeypatch.setattr(reporting, "_gh_api", lambda *a: pytest.fail("must not post"))
    assert reporting.send_report(reporting.make_report("unexpected")) == ""


def test_live_github_is_impossible_in_tests():
    with pytest.raises(RuntimeError, match="disabled during tests"):
        reporting._gh_api("user")


def test_hardware_can_be_unknown_without_claiming_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(onboarding, "_ram_bytes", lambda: None)
    monkeypatch.setattr(onboarding.shutil, "which", lambda *a: None)
    monkeypatch.setattr(onboarding.platform, "system", lambda: "Linux")
    monkeypatch.setattr(onboarding, "models_root", lambda: tmp_path / "missing" / "models")
    hardware = onboarding.inspect_hardware()
    assert hardware.ram_gb is None and hardware.disk_free_gb > 0
    assert "RAM: not detected" in hardware.summary()


@pytest.fixture
def wizard(qt_app, isolated, monkeypatch):
    from hugmunn.ui.setup_wizard import SetupWizard
    monkeypatch.setattr(SetupWizard, "_run", lambda *a: None)
    monkeypatch.setattr("hugmunn.core.cli.is_signed_in", lambda p: False)
    return SetupWizard(config.Settings())


def test_wizard_ends_with_unchecked_agreement(wizard, isolated):
    wizard.show()
    assert wizard.pageIds() == [0, 1, 2, 3, 4]
    wizard.accept()
    assert not (isolated / "settings.json").exists()
    while wizard.currentId() < 4:
        wizard.next()
    assert wizard.currentPage() is wizard.agreement_page
    assert not wizard.agree.isChecked()
    assert wizard.report_choice.isChecked() and wizard.final_reports.isChecked()
    assert not wizard.button(wizard.WizardButton.FinishButton).isEnabled()
    wizard.agree.setChecked(True)
    wizard.final_reports.setChecked(False)
    wizard.accept()
    saved = config.Settings.load()
    assert not onboarding.needs_setup(saved)
    assert saved.automatic_reports is False


def test_cancelling_does_not_accept_or_save_theme(wizard, isolated):
    from hugmunn.ui import theme
    original = dict(theme.active())
    wizard.theme_choice.setCurrentIndex(wizard.theme_choice.findData("light"))
    wizard.reject()
    assert not (isolated / "settings.json").exists()
    assert onboarding.needs_setup(wizard.settings)
    assert theme.active() == original


def test_github_verification_updates_selected_account(wizard):
    wizard._received("github", "alice")
    assert wizard._github_account == "alice"
    wizard._disconnect()
    assert wizard._github_account == ""


def test_setup_license_matches_distribution():
    from pathlib import Path
    from importlib.resources import files
    bundled = files("hugmunn").joinpath("resources", "LICENSE.txt").read_text()
    assert bundled == Path(__file__).resolve().parents[1].joinpath("LICENSE").read_text()


def test_disconnect_ignores_a_late_account_check(wizard):
    wizard._disconnect()
    wizard._received("github", "alice")
    assert wizard._github_account == ""


def test_github_browser_login_process_finishes_cleanly(wizard, qt_app, tmp_path, monkeypatch):
    import os
    import sys
    import time
    if os.name == "nt":
        pytest.skip("POSIX test helper; application QProcess is cross-platform")
    from hugmunn.ui import setup_wizard
    helper = tmp_path / "gh"
    helper.write_text(f"#!{sys.executable}\n" +
        "import sys, time\nprint('First copy your one-time code: ABCD-1234', flush=True)\n" +
        "sys.stdin.readline()\ntime.sleep(0.1)\n")
    helper.chmod(0o755)
    monkeypatch.setattr(setup_wizard.shutil, "which", lambda n: str(helper))
    monkeypatch.setattr(wizard, "_check_github", lambda: wizard._received("github", "alice"))
    wizard._github_login()
    deadline = time.monotonic() + 5
    code_seen = False
    while wizard._login is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        code_seen |= "ABCD-1234" in wizard.device_code.text()
        time.sleep(0.01)
    assert code_seen
    assert wizard._login is None and wizard._github_account == "alice"
    assert wizard.login_button.isEnabled()
