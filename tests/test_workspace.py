import csv
import io
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QLineEdit

from ctrlcctrlv.app import Controller
from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.custom_models import ModelRegistry, discover, make_model
from ctrlcctrlv.formatting import format_text
from ctrlcctrlv.history import History
from ctrlcctrlv.remote_catalog import VisionModel, fetch_models, load_cache, save_cache, parse_models
from ctrlcctrlv.review import ReviewProvider, ReviewResult
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.table_ocr import table_result


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    c = Controller(app, tmp_path, native=False)
    yield c
    c.cancel()
    c.pool.waitForDone(5000)
    app.processEvents()
    c.tray.hide()
    for timer in (c.watchdog, c.idle_timer, c.permissions_timer, c.capture_timer, c.typing_timer, c.preview.syntax_timer):
        timer.stop()
    for widget in (c.window, c.preview, c.indicator, c.diagnostics_window):
        widget.close()
        widget.deleteLater()
    c.deleteLater()


def wait(c, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        c.app.processEvents()
        time.sleep(.002)
    c.app.processEvents()
    assert condition()


def test_provider_migration_and_independent_roundtrip(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"provider": "openrouter"}), encoding="utf-8")
    assert Settings.load(tmp_path).review_provider == "openrouter"
    settings = Settings(provider="local", review_provider="openrouter", capture_format="csv")
    settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings


def test_history_preserves_literals_survives_restart_and_deletes_only_selected(tmp_path):
    history = History(tmp_path)
    original = '  SELECT "Ж", \'x,y\';\r\n\t😀\n'
    first = history.add("ocr", original, "local-model")
    second = history.add("typing", original, status="cancelled")
    restored = History(tmp_path)
    assert [row.id for row in restored.entries()] == [second, first]
    assert restored.entries("x,y")[0].text == original
    restored.delete(first)
    assert [row.id for row in restored.entries()] == [second]
    assert original.encode() in restored.path.read_bytes()
    restored.delete(second)
    assert restored.entries() == []
    assert original.encode() not in restored.path.read_bytes()


def test_vlm_catalog_filters_text_only_and_shows_prices_without_generation(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [
            {"id": "cheap/vision", "name": "Cheap", "architecture": {"input_modalities": ["image", "text"], "output_modalities": ["text"]}, "supported_parameters": ["response_format"], "pricing": {"prompt": ".00000025", "completion": ".00000075"}},
            {"id": "text/only", "name": "Text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
            {"id": "image/generator", "name": "Images", "architecture": {"input_modalities": ["image"], "output_modalities": ["image"]}}
        ]})
    models = fetch_models(httpx.MockTransport(handler))
    assert len(models) == 1 and models[0].structured
    assert "$0.25 / $0.75" in models[0].label
    assert calls[0].method == "GET" and "authorization" not in calls[0].headers
    save_cache(tmp_path, models)
    assert load_cache(tmp_path) == models


def test_vision_model_without_structured_output_uses_prompt_contract():
    from ctrlcctrlv.ocr import OCRProvider
    from ctrlcctrlv.core import Operation
    def handler(request):
        payload = json.loads(request.content)
        assert "response_format" not in payload
        assert "JSON" in payload["messages"][0]["content"][0]["text"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"text":"exact"}'}}]})
    assert OCRProvider("https://example.org/ocr", "vision", "test", response_format=False).recognize(b"png", Operation(), httpx.MockTransport(handler)) == "exact"


def test_custom_gguf_catalog_is_pinned_and_profile_specific(tmp_path):
    commit = "a" * 40
    def handler(request):
        assert request.url.params["blobs"] == "true"
        return httpx.Response(200, json={"sha": commit, "siblings": [
            {"rfilename": "weights-Q4.gguf", "lfs": {"sha256": "b" * 64, "size": 1000}},
            {"rfilename": "mmproj.gguf", "lfs": {"sha256": "c" * 64, "size": 200}}]})
    info = discover("https://huggingface.co/publisher/vision/resolve/main/weights-Q4.gguf", httpx.MockTransport(handler))
    model = make_model(info, info["selected"], info["projectors"][0])
    assert all(commit in file.url for file in model.files)
    registry = ModelRegistry(tmp_path)
    registry.add(model)
    assert model == ModelRegistry(tmp_path).models[model.id]
    assert model.id not in MODELS
    assert [file.filename for file in model.files] == ["model.gguf", "mmproj.gguf"]
    with pytest.raises(ValueError):
        discover("https://huggingface.co/publisher/../../secret")


@pytest.mark.parametrize("language,source,expected", [
    ("python", 'def f(x):\n return "Ж"+str(x)\n', '    return "Ж" + str(x)'),
    ("sql", "select a,b from t where x='keep  spaces';", "'keep  spaces'"),
    ("java", 'public class X{public String f(){return "keep  spaces";}}', 'return "keep  spaces";'),
    ("json", '{"n":123456789012345678901,"x":1.234567890123456789,"n":2}', '1.234567890123456789')
])
def test_formatters_preserve_literals(language, source, expected):
    formatted = format_text(source, language)
    assert formatted != source
    assert expected in formatted
    assert formatted.endswith("\n") == source.endswith("\n")
    if language == "json":
        assert formatted.count('"n"') == 2


@pytest.mark.parametrize("source", ['def f(\n', '{"x": }'])
def test_invalid_source_is_not_silently_repaired(source):
    with pytest.raises(Exception):
        format_text(source, "python" if source.startswith("def") else "json")


@pytest.mark.parametrize("source", [
    '| Name | Value | Empty |\n| --- | --- | --- |\n| Ж | a,b | |\n| 😀 | "quoted" | x |',
    '<table><tr><th>Name</th><th>Value</th><th>Empty</th></tr><tr><td>Ж</td><td>a,b</td><td></td></tr><tr><td>😀</td><td>&quot;quoted&quot;</td><td>x</td></tr></table>'
])
def test_whole_table_becomes_csv_without_losing_empty_cells_or_quotes(source):
    assert list(csv.reader(io.StringIO(table_result(source)))) == [["Name", "Value", "Empty"], ["Ж", "a,b", ""], ["😀", '"quoted"', "x"]]
    assert table_result("Before\n" + source) == "Before\n" + source
    assert table_result(source, "text") == source


def test_ragged_csv_is_preserved_for_text_fallback():
    assert table_result("a,b\n1,2,3\n", "csv") == "a,b\n1,2,3\n"
    prose = "def greet(name):\n    print(name)\n"
    assert table_result(prose) == prose


def test_lighton_wrapped_markdown_table_and_structural_annotations():
    source = '<table>\n### Table - Employee Data\n*Type: data*\n\n| ID | Name |\n|---|---|\n|1001|Alice|\n</table>'
    assert table_result(source) == 'ID,Name\n1001,Alice\n'
    unrelated = source.replace('### Table - Employee Data', 'Some visible prose')
    assert table_result(unrelated) == unrelated


def test_work_layout_and_copy_close_history_and_undo_format(controller):
    c = controller
    assert [c.window.navigation.item(i).text() for i in range(4)] == ["Работа", "Модели", "Настройки", "История"]
    c.window.show_configuration("typing")
    assert c.window.pages.currentIndex() == 2 and c.window.configuration.currentIndex() == 1
    c.preview.display(QImage(10, 10, QImage.Format.Format_RGB32), "x=1", .1, "test")
    revision = c.preview.syntax_revision
    assert c.preview.apply_format("x = 1", revision)
    c.preview.text.undo()
    assert c.preview.text.toPlainText() == "x=1"
    c.preview.text.setPlainText("edited\n\t😀")
    c.preview.copy_button.click()
    assert not c.preview.isVisible()
    assert c.app.clipboard().text() == "edited\n\t😀"
    assert c.history.entries()[0].text == c.app.clipboard().text()
    item = c.window.history_page.entries.currentItem()
    identifier = item.data(Qt.ItemDataRole.UserRole).id
    c.window.history_page.delete.click()
    assert all(entry.id != identifier for entry in c.history.entries())


@pytest.mark.parametrize("ocr_provider,review_provider", [("local", "openrouter"), ("openrouter", "local")])
def test_mixed_review_providers_and_context_do_not_replace_clipboard(controller, monkeypatch, ocr_provider, review_provider):
    c = controller
    c.window.provider.setCurrentIndex(c.window.provider.findData(ocr_provider))
    c.window.review_provider.setCurrentIndex(c.window.review_provider.findData(review_provider))
    c.credentials = SimpleNamespace(read=lambda: "test-key")
    model = VisionModel(c.settings.review_remote_model, "Vision", "0.25", "0.75", True)
    c.remote_models = {model.id: model}
    c.review_runtime.installed = lambda identifier: True
    c.review_runtime.start = lambda *args: None
    c.review_runtime.endpoint = "http://127.0.0.1:1"
    c.review_runtime.key = "local-key"
    c.review_runtime.stop = lambda: None
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    c.preview.display(image, "value=O", .1, "ocr")
    c.preview.review_context.setPlainText("Customer ID uses digits")
    c.app.clipboard().setText("keep clipboard")
    calls = []
    def inspect(provider, png, original, operation, *, details):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((provider.provider.local, details))
        return ReviewResult(original, "value=0", "checker", .1)
    monkeypatch.setattr(ReviewProvider, "inspect", inspect)
    c.review_text()
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert calls == [(review_provider == "local", "Customer ID uses digits")]
    assert c.preview.review_result.proposed == "value=0"
    assert c.app.clipboard().text() == "keep clipboard"


def test_indicator_does_not_expand_or_focus_main_window(controller):
    from ctrlcctrlv.core import Operation
    c = controller
    c.window.hide()
    c.operation = Operation()
    c.status("OCR")
    assert c.indicator.isVisible() and not c.window.isVisible()
    assert c.indicator.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert c.indicator.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    c.indicator.set_progress(5, 10)
    assert c.indicator.progress.value() == 50
    c.operation = None
    c.status("Completed")
    assert not c.indicator.isVisible()


@pytest.mark.parametrize("flag", ["typing_pending", "typing_cancel", "model_cancel", "review_cancel", "diagnostics_cancel"])
def test_indicator_is_absent_for_non_ocr_operations(controller, flag):
    c = controller
    setattr(c, flag, True if flag == "typing_pending" else threading.Event())
    c.status("Busy")
    assert c.busy()
    assert not c.indicator.isVisible()
    setattr(c, flag, None if flag != "typing_pending" else False)


def test_cancelled_ocr_hides_indicator_before_worker_finishes(controller):
    from ctrlcctrlv.core import Operation
    c = controller
    c.operation = c.ocr_worker_operation = Operation()
    c.status("OCR")
    assert c.indicator.isVisible()
    c.cancel()
    assert c.busy() and not c.indicator.isVisible()
    c.ocr_worker_operation = None


def test_model_chooser_search_select_and_cancel_preserve_identifiers(controller):
    from ctrlcctrlv.workspace_ui import ModelPicker
    picker = ModelPicker("vendor/old")
    picker.set_models([VisionModel("vendor/old", "Older vision", "0.1", "0.2", True),
                       VisionModel("vendor/new", "A long but readable model name", "0.2", "0.4", True)])
    dialog = picker.create_chooser()
    dialog.search.setText("vendor/new")
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "A long but readable model name"
    assert dialog.table.item(0, 1).text() == "$0.2 / $0.4"
    dialog.reject()
    assert picker.currentData() == "vendor/old"
    dialog = picker.create_chooser()
    dialog.search.setText("vendor/new")
    dialog.table.selectRow(0)
    dialog.accept_selected()
    assert picker.currentData() == "vendor/new"
    assert picker.currentText() == "A long but readable model name"
    assert not picker.isEditable()
    dialog.deleteLater()
    picker.deleteLater()


def test_library_actions_do_not_change_ocr_binding(controller):
    c = controller
    ocr = c.settings.local_model
    identifier = "qwen3-vl-4b"
    c.window.managed_model.setCurrentIndex(c.window.managed_model.findData(identifier))
    installed = []
    c.runtime.install = lambda model, cancel, progress: installed.append(model)
    c.model_job(identifier=c.window.managed_model.currentData())
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert installed == [identifier]
    assert c.settings.local_model == ocr


def test_history_retyping_uses_selected_text_without_overwriting_clipboard(controller):
    c = controller
    c.platform = SimpleNamespace(permissions=lambda: {"accessibility": True, "screen": False, "input_monitoring": False})
    text = "history\n\t😀"
    c.record_history("ocr", text)
    c.app.clipboard().setText("other clipboard")
    typed = []
    c.start_typing = typed.append
    c.window.history_page.type.click()
    assert c.typing_pending and c.pending_typing_text == text
    c.finish_typing_countdown()
    assert typed == [text]
    assert c.app.clipboard().text() == "other clipboard"


def test_model_table_selection_and_row_actions_keep_ocr_binding(controller):
    c, window = controller, controller.window
    assert window.model_table.rowCount() == len(MODELS)
    assert not window.download_selected.isEnabled()
    window.toggle_all_models(0)
    assert window.checked_models() == list(MODELS)
    assert window.download_selected.isEnabled()
    window.toggle_all_models(0)
    assert window.checked_models() == []
    identifier = "qwen3-vl-4b"
    row, (load, delete) = window.model_rows[identifier]
    window.update_model_states({identifier}, set(), True, False)
    window.model_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert window.delete_selected.isEnabled()
    assert load.text() == "Подготовить" and delete.isEnabled()
    requests = []
    window.model_action_requested.disconnect()
    window.model_action_requested.connect(lambda model, action: requests.append((model, action)))
    load.click()
    assert requests == [(identifier, "start")]
    assert c.settings.local_model == "lightonocr-3-0.8b"


def test_bulk_download_runs_in_one_worker_and_stops_on_error(controller):
    c, calls = controller, []
    def install(identifier, token, progress):
        assert threading.current_thread() is not threading.main_thread()
        calls.append(identifier)
        if len(calls) == 2:
            raise RuntimeError("download failed")
    c.runtime.install = install
    c.download_models(list(MODELS))
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert calls == list(MODELS)[:2]
    assert "1/4" in c.window.model_error.text()
    assert "download failed" in c.window.model_error.text()
    assert c.window.model_error.toolTip() == "Загружено 1/4 · download failed"
    assert not c.window.model_error.isHidden()
    assert not c.window.model_retry_button.isHidden()
    assert c.window.model_retry_button.isEnabled()
    assert c.window.navigation.currentRow() == 1
    assert c.settings.local_model == "lightonocr-3-0.8b"


def test_bulk_delete_releases_reviewer_and_removes_only_requested_models(controller):
    c = controller
    models = list(MODELS)[:2]
    c.runtime.installed = lambda identifier: True
    c.review_runtime.model_id = models[1]
    events = []
    c.review_runtime.stop = lambda: events.append("stop-review")
    c.runtime.delete = lambda identifier: events.append(identifier)
    c.delete_models(models)
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert events == [models[0], "stop-review", models[1]]


def test_refine_uses_selected_cloud_model_without_changing_saved_settings(controller, monkeypatch):
    from ctrlcctrlv.ocr import OCRProvider
    c = controller
    model = VisionModel("cheap/vision", "Cheap vision", "0.1", "0.2", False)
    c.remote_models = {model.id: model}
    c.credentials = SimpleNamespace(read=lambda: "test-key")
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    c.preview.display(image, "old", .1, "previous")
    c.refresh_refine_models(("openrouter", model.id))
    calls = []
    def recognize(provider, png, operation, transport=None, **kwargs):
        calls.append((provider.model, provider.local))
        return "new result"
    monkeypatch.setattr(OCRProvider, "recognize", recognize)
    c.review_text()
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert calls == [(model.id, False)]
    assert c.preview.review_result.proposed == "new result"
    assert c.preview.text.toPlainText() == "old"
    assert c.settings.provider == "local"
    assert c.settings.remote_model == "qwen/qwen3-vl-8b-instruct"
    assert c.preview.refine_model.currentData() == ("openrouter", model.id)


def test_refine_prepares_selected_local_model_in_worker_then_reads_same_image(controller, monkeypatch):
    from ctrlcctrlv.ocr import OCRProvider
    c = controller
    identifier = "qwen3-vl-4b"
    c.review_runtime.installed = lambda model: model == identifier
    c.review_runtime.endpoint = "http://127.0.0.1:1"
    c.review_runtime.key = "local-key"
    events = []
    def start(model, token, progress):
        assert threading.current_thread() is not threading.main_thread()
        events.append(("start", model))
        c.review_runtime.model_id, c.review_runtime.ready = model, True
    c.review_runtime.start = start
    monkeypatch.setattr(OCRProvider, "recognize", lambda provider, png, op, transport=None, **kwargs: events.append(("ocr", provider.model)) or "same image")
    image = QImage(13, 17, QImage.Format.Format_RGB32)
    c.preview.display(image, "old", .1, "previous")
    c.refresh_refine_models(("local", identifier))
    c.review_text()
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert events == [("start", identifier), ("ocr", identifier)]
    assert c.preview.source_image.size() == image.size()
    assert c.settings.local_model == "lightonocr-3-0.8b"


def test_remote_capabilities_restrict_tokens_and_omit_unsupported_parameters(controller):
    from ctrlcctrlv.core import Operation, OperationError
    c = controller
    c.credentials = SimpleNamespace(read=lambda: "test-key")
    models = parse_models({"data": [{"id": "vision", "name": "Vision", "architecture": {"input_modalities": ["image"], "output_modalities": ["text"]},
                                    "supported_parameters": [], "top_provider": {"max_completion_tokens": 512}}]})
    c.remote_models = {model.id: model for model in models}
    provider = c.remote_provider("vision")
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"text": "ok"}'}}]})
    assert provider.recognize(b"png", Operation(), httpx.MockTransport(respond)) == "ok"
    payload = requests[0]
    assert payload["max_tokens"] == 512
    assert "temperature" not in payload and "response_format" not in payload
    assert payload["modalities"] == ["text"]
    with pytest.raises(OperationError, match="vision-модель"):
        c.remote_provider("text/only")


@pytest.mark.parametrize("reason,expected", [("Unsupported response_format json_object", "JSON-формат"),
                                            ("max_tokens limit", "лимит модели"),
                                            ("image input not supported", "не принимает изображения"),
                                            ("bad request", "отклонил запрос")])
def test_http400_explains_failure_without_echoing_private_body(controller, reason, expected):
    from ctrlcctrlv.core import Operation, OperationError
    from ctrlcctrlv.ocr import OCRProvider
    provider = OCRProvider("https://example.test", "vision", "dummy")
    private = "private screenshot text"
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"message": reason, "private": private}}))
    controller.app.clipboard().setText("untouched")
    with pytest.raises(OperationError) as error:
        provider.recognize(b"png", Operation(), transport)
    assert expected in str(error.value)
    assert private not in str(error.value)
    assert controller.app.clipboard().text() == "untouched"


def test_indicator_is_compact_tracks_time_and_resets(controller):
    c = controller
    c.indicator.display("Verbose text that must never be displayed")
    assert c.indicator.size().width() <= 220 and c.indicator.size().height() <= 40
    c.indicator.started = time.monotonic() - 65
    c.indicator.update_elapsed()
    assert c.indicator.elapsed.text() == "01:05"
    c.indicator.hide()
    assert not c.indicator.timer.isActive()
    c.indicator.display("Next")
    assert c.indicator.elapsed.text() == "00:00"


def test_closing_preview_cancels_review_and_rejects_late_proposal(controller):
    c = controller
    c.preview.display(QImage(12, 12, QImage.Format.Format_RGB32), "old", .1, "model")
    pending = c.review_cancel = threading.Event()
    c.copy_edited("old")
    assert pending.is_set()
    assert c.review_cancel is pending  # worker still owns its inference slot
    assert not c.preview.propose(ReviewResult("old", "new", "model", .1))
    assert not c.preview.review_panel.isVisible()
    assert c.app.clipboard().text() == "old"


def test_refinement_uses_selected_model_image_text_and_prompt_without_changing_defaults(controller, monkeypatch):
    c = controller
    model = VisionModel("test/vision", "Test Vision", "0.1", "0.2", True)
    c.remote_models = {model.id: model}
    c.credentials = SimpleNamespace(read=lambda: "test-key")
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    c.preview.display(image, "value = O", .1, "light-model")
    c.preview.review_context.setPlainText("Сверь ноль и букву O")
    c.refresh_refine_models(("openrouter", model.id))
    defaults = (c.settings.provider, c.settings.local_model, c.settings.review_provider, c.settings.review_remote_model)
    c.app.clipboard().setText("value = O")
    calls = []
    def inspect(provider, png, text, operation, **kwargs):
        calls.append((provider.provider.model, png, text, kwargs["details"]))
        return ReviewResult(text, "value = 0", model.id, .1)
    monkeypatch.setattr(ReviewProvider, "inspect", inspect)
    c.preview.review_button.click()
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert len(calls) == 1
    assert calls[0][0] == model.id
    assert calls[0][1].startswith(b"\x89PNG")
    assert calls[0][2:] == ("value = O", "Сверь ноль и букву O")
    assert c.preview.review_result.proposed == "value = 0"
    assert c.app.clipboard().text() == "value = O"
    assert defaults == (c.settings.provider, c.settings.local_model, c.settings.review_provider, c.settings.review_remote_model)


def test_refinement_commits_active_csv_cell_and_requests_csv(controller, monkeypatch):
    c = controller
    model = VisionModel("test/vision", "Test Vision", "0.1", "0.2", True)
    c.remote_models = {model.id: model}
    c.credentials = SimpleNamespace(read=lambda: "test-key")
    c.preview.display(QImage(12, 12, QImage.Format.Format_RGB32), "Name,Qty\nPaper,1\n", .1, "light")
    c.refresh_refine_models(("openrouter", model.id))
    c.preview.table.editItem(c.preview.table.item(1, 1))
    c.preview.table.findChild(QLineEdit).setText("999")
    calls = []
    def inspect(provider, png, text, operation, **kwargs):
        calls.append((text, kwargs["format_mode"]))
        return ReviewResult(text, text, model.id, .1)
    monkeypatch.setattr(ReviewProvider, "inspect", inspect)
    c.review_text()
    wait(c, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
    assert calls == [("Name,Qty\nPaper,999\n", "csv")]


def test_minimizing_preview_keeps_pending_review(controller):
    c = controller
    c.preview.display(QImage(12, 12, QImage.Format.Format_RGB32), "old", .1, "model")
    pending = c.review_cancel = threading.Event()
    c.preview.showMinimized()
    c.app.processEvents()
    assert not pending.is_set()
    c.preview.showNormal()
    c.preview.close()
    assert pending.is_set()


def test_refine_warmup_has_inline_feedback_and_cancel_without_overlay(controller, monkeypatch):
    c = controller
    identifier = "qwen3-vl-4b"
    c.review_runtime.installed = lambda model: model == identifier
    c.preview.display(QImage(12, 12, QImage.Format.Format_RGB32), "old", .1, "model")
    c.refresh_refine_models(("local", identifier))
    jobs = []
    monkeypatch.setattr(c, "run_job", lambda *args: jobs.append(args))
    c.review_text()
    assert "Подготовка" in c.preview.status.text()
    assert c.preview.review_cancel_button.isVisible()
    assert not c.indicator.isVisible()
    pending = c.review_cancel
    c.preview.review_cancel_button.click()
    assert pending.is_set()
    assert c.review_cancel is pending
    jobs[0][2]("cancelled")
    assert not c.busy()
    assert not c.preview.review_cancel_button.isVisible()
    assert "отменена" in c.preview.status.text()
