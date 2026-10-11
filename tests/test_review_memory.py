import threading

import pytest

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.hardware import InsufficientMemory
from ctrlcctrlv.review import local_review_session


class Runtime:
    def __init__(self, name, calls, ready=False):
        self.name, self.calls = name, calls
        self.ready = ready
        self.model_id = "ocr" if ready else None
        self.loaded_acceleration = "gpu" if ready else None
        self.acceleration = "gpu"
        self.failure = None
        self.primary = None

    def start(self, model, cancel, progress):
        self.calls.append((self.name, "start", model))
        if self.failure:
            raise self.failure
        if self.primary and self.primary.ready:
            raise InsufficientMemory("two models cannot fit")
        self.ready = True
        self.model_id = model
        self.loaded_acceleration = self.acceleration

    def stop(self):
        self.calls.append((self.name, "stop"))
        self.ready = False
        self.model_id = None
        self.loaded_acceleration = None


def test_models_that_cannot_fit_are_loaded_sequentially_and_ocr_restored():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    reviewer.primary = primary
    with local_review_session(primary, reviewer, "checker", "gpu", threading.Event(), lambda *a: None, errors) as active:
        assert active is reviewer
        assert active.ready and not primary.ready
    assert primary.ready and primary.model_id == "ocr"
    assert not reviewer.ready
    assert not errors
    assert calls == [("review", "start", "checker"), ("ocr", "stop"),
                     ("review", "start", "checker"), ("review", "stop"), ("ocr", "start", "ocr")]


def test_same_model_is_reused_without_unloading_ocr():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    with local_review_session(primary, reviewer, "ocr", "gpu", threading.Event(), lambda *a: None, errors) as active:
        assert active is primary
    assert primary.ready
    assert calls == [("review", "stop")]


def test_driver_error_never_unloads_primary_as_a_memory_retry():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    reviewer.failure = OperationError("driver unavailable")
    with pytest.raises(OperationError, match="driver"):
        with local_review_session(primary, reviewer, "checker", "gpu", threading.Event(), lambda *a: None, errors):
            pytest.fail("No model is ready")
    assert primary.ready and not errors
    assert ("ocr", "stop") not in calls


def test_cancel_does_not_restore_or_continue_loading_a_model():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    reviewer.primary = primary
    cancel = threading.Event()
    with local_review_session(primary, reviewer, "checker", "gpu", cancel, lambda *a: None, errors):
        cancel.set()
    assert not primary.ready and not reviewer.ready
    assert calls[-1] == ("review", "stop")


def test_restore_failure_is_reported_without_losing_checked_text():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    reviewer.primary = primary
    with local_review_session(primary, reviewer, "checker", "gpu", threading.Event(), lambda *a: None, errors):
        primary.failure = OperationError("restore failed")
        result = "checked text"
    assert result == "checked text"
    assert errors == ["restore failed"]
    assert not reviewer.ready


def test_unknown_global_gpu_capacity_checks_sequentially_then_restores():
    calls, errors = [], []
    primary, reviewer = Runtime("ocr", calls, True), Runtime("review", calls)
    primary.hardware = {"devices": [{"memory_source": "CUDA budget; global memory unknown"}]}
    with local_review_session(primary, reviewer, "checker", "gpu", threading.Event(), lambda *a: None, errors):
        assert not primary.ready and reviewer.ready
    assert primary.ready and not errors
    assert calls[0] == ("ocr", "stop")
    assert calls[-1] == ("ocr", "start", "ocr")
