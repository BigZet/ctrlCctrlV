import os
import pytest

# GUI tests must never capture the user's screen or change the real clipboard.
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(autouse=True)
def dispose_queued_qt_objects():
    yield
    # QApplication is shared across tests. Deliver deferred QObject deletion so
    # owned timers from a previous controller cannot leak into the next test.
    from PySide6.QtCore import QCoreApplication, QEvent
    if QCoreApplication.instance():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
