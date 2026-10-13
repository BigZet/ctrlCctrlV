import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.settings import Settings


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    c = Controller(app, tmp_path, native=False)
    yield c
    c.pool.waitForDone()
    for timer in (c.watchdog, c.idle_timer, c.permissions_timer):
        timer.stop()
    if c.setup_wizard:
        c.setup_wizard.hide()
    c.tray.hide()
    c.window.hide()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("provider,acceleration", [("local", "cpu"), ("openrouter", "gpu")])
def test_first_run_downloads_or_connects_only_after_explicit_finish(controller, provider, acceleration):
    c = controller
    calls = []
    c.run_job = lambda *args, **kwargs: pytest.fail("No OCR or implicit jobs during setup")
    c.install = lambda: calls.append("download")
    c.refresh_catalog = lambda: calls.append("catalog")
    c.credentials.write = lambda value: calls.append(("key", value))
    c.show_startup()
    wizard = c.setup_wizard
    wizard.provider.setCurrentIndex(wizard.provider.findData(provider))
    wizard.acceleration.setCurrentIndex(wizard.acceleration.findData(acceleration))
    wizard.next()
    assert calls == []
    if provider == "openrouter":
        wizard.key.setText("synthetic-onboarding-key")
    wizard.accept()
    saved = Settings.load(c.directory)
    assert saved.provider == provider and saved.acceleration == acceleration
    assert not c.first_run and c.window.isVisible()
    assert not c.runtime.ready
    assert calls == (["download"] if provider == "local" else [("key", "synthetic-onboarding-key"), "catalog"])


def test_deferred_setup_does_not_write_preferences(controller):
    c = controller
    c.show_startup()
    c.setup_wizard.reject()
    assert not (c.directory / "settings.json").exists()
    assert c.window.isVisible()


def test_existing_install_opens_settings_without_new_onboarding(controller):
    c = controller
    Settings(provider="openrouter", acceleration="cpu").save(c.directory)
    c.first_run = False
    c.show_startup()
    assert c.setup_wizard is None and c.window.isVisible()
