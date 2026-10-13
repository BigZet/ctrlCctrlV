import os
import sys
import pytest

# GUI tests must never capture the user's screen or change the real clipboard.
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture
def physical_control_modifier():
    """Qt represents the physical Control key as Meta on macOS."""
    from PySide6.QtCore import Qt
    return Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin" else Qt.KeyboardModifier.ControlModifier


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    # Keep Qt alive until every test-owned top-level window has been destroyed.
    # Python shutdown otherwise destroys surviving dialogs/documents in an
    # undefined order, which can crash the Windows Qt runtime after pytest exits.
    from PySide6.QtCore import QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    QThreadPool.globalInstance().waitForDone()
    for window in app.topLevelWidgets():
        window.close()
        window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def dispose_queued_qt_objects():
    yield
    # QApplication is shared across tests. Deliver deferred QObject deletion so
    # owned timers from a previous controller cannot leak into the next test.
    from PySide6.QtCore import QCoreApplication, QEvent
    if QCoreApplication.instance():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
