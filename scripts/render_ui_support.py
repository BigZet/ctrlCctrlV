"""Explicit virtual screen and per-frame evidence for the synthetic UI renderer."""
import fnmatch
import json
import os
from pathlib import Path

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QAbstractButton, QComboBox, QDialog

BASELINE_STATES = {
    'preview-search': 'preview', 'preview-text-manual': 'edited', 'preview-zoom': 'preview',
    'preview-image-hidden': 'preview', 'preview-empty': 'preview', 'raw-response': 'preview',
    'prompt-expanded': 'context', 'table-search': 'csv-preview', 'table-commands': 'csv-edited',
    'table-commands-menu': 'csv-edited', 'csv-text-manual': 'csv-preview',
    'history-pins-undo': 'history-populated', 'history-error-undo': 'history-populated',
    'history-search-empty': 'history', 'review-narrow': 'review', 'review-narrow-changes': 'review',
    'model-queue-unknown-total': 'model-download', 'model-error-retry': 'models',
    'models-30': 'models', 'models-30-queue-error': 'model-download', 'hotkey-recording': 'system',
    'setup-hardware': 'setup', 'diagnostics-collapsed': 'diagnostics',
    'diagnostics-expanded': 'diagnostics', 'diagnostics-unknown': 'diagnostics',
    'diagnostics-error': 'diagnostics', 'add-model-discovered': 'add-model',
    'add-model-expanded': 'add-model', 'work-repeat-region': 'work',
    'ocr-error-expanded': 'ocr-retry', 'hardware-details': 'hardware',
    'cloud-connected': 'cloud', 'cloud-key-edit': 'cloud',
    'model-chooser-favorites': 'model-chooser', 'model-chooser-downloaded': 'model-chooser',
    'model-chooser-cloud': 'model-chooser', 'model-chooser-missing': 'model-chooser',
}


def rectangle(value):
    return dict(x=value.x(), y=value.y(), width=value.width(), height=value.height())


class RenderSession:
    def __init__(self, app, args):
        self.app, self.args = app, args
        self.available = QRect(0, 0, 960, 500) if args.small_screen else QRect(0, 0, 1600, 1100)
        self.mode = 'compact-small-screen' if args.small_screen else 'compact' if args.compact else 'default'
        self.scale_name = f'scale{round(args.scale * 100)}'
        self.theme = 'light'
        self.frames = []
        self.encountered = set()
        self._original_fits = []
        from ctrlcctrlv import ui, workspace_ui, model_picker, theme
        original = theme.fit_window_to_screen
        for module in (ui, workspace_ui, model_picker, theme):
            self._original_fits.append((module, module.fit_window_to_screen))
            module.fit_window_to_screen = lambda window, available=None: original(window, self.available)
        from ctrlcctrlv import hardware
        self._hardware = hardware
        self._original_memory = hardware.memory_capacity
        hardware.memory_capacity = lambda: (64 * 1024**3, 48 * 1024**3)

    def register(self, window, requested=None):
        requested = QSize(*requested) if isinstance(requested, tuple) else requested
        window._render_requested_size = QSize(requested or window.size())
        return window

    def capture(self, window, state, requested=None, fit=True):
        if self.args.trace:
            print(f'Preparing {self.theme}/{state}', flush=True)
        self.encountered.add(state)
        if self.args.states and not any(fnmatch.fnmatchcase(state, pattern) for pattern in self.args.states):
            return
        if requested is not None:
            self.register(window, requested)
        self.app.processEvents()
        if fit:
            from ctrlcctrlv.theme import fit_window_to_screen
            fit_window_to_screen(window, self.available)
        self.app.processEvents()
        pixmap = window.grab()
        alpha_background = None
        from PySide6.QtWidgets import QMenu
        if isinstance(window, QMenu) and pixmap.hasAlphaChannel():
            # The offscreen popup grab can leave the native backing transparent.
            # Supply its theme panel only for the artifact, and record that fact.
            from PySide6.QtGui import QPixmap, QPainter, QColor
            from ctrlcctrlv.theme import colours
            flat = QPixmap(pixmap.size())
            flat.setDevicePixelRatio(pixmap.devicePixelRatio())
            flat.fill(QColor(colours()['panel']))
            painter = QPainter(flat)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = flat
            alpha_background = 'theme_panel_for_offscreen_popup'
        name = f'{state}-{self.theme}-{self.scale_name}-{self.mode}'
        path = self.args.output / f'{name}.png'
        if not pixmap.save(str(path)):
            raise OSError(f'Cannot save {path}')
        # Existing automation and reports still find their original filenames.
        legacy = self.args.output / f'{self.theme}-{state}.png'
        pixmap.save(str(legacy))
        requested_size = getattr(window, '_render_requested_size', window.size())
        screen = self.app.primaryScreen()
        controls = []
        for control in window.findChildren(QAbstractButton) + window.findChildren(QComboBox):
            if not control.isVisibleTo(window):
                continue
            rect = QRect(control.mapTo(window, control.rect().topLeft()), control.size())
            controls.append(dict(label=control.accessibleName() or control.text() if isinstance(control, QAbstractButton) else control.accessibleName() or control.currentText(),
                                 kind=control.__class__.__name__, enabled=control.isEnabled(),
                                 geometry=rectangle(rect), inside_client=window.rect().contains(rect)))
        metadata = dict(state=state, theme=self.theme, scale=self.args.scale, mode=self.mode,
                        screenshot=path.name, legacy_screenshot=legacy.name,
                        window_class=window.__class__.__name__, window_title=window.windowTitle(),
                        requested_size=dict(width=requested_size.width(), height=requested_size.height()),
                        client_geometry=rectangle(window.geometry()), frame_geometry=rectangle(window.frameGeometry()),
                        available_geometry=rectangle(self.available),
                        actual_offscreen_geometry=rectangle(screen.geometry()),
                        actual_offscreen_available=rectangle(screen.availableGeometry()),
                        device_pixel_ratio=window.devicePixelRatioF(),
                        image_size=dict(width=pixmap.width(), height=pixmap.height()),
                        fit_applied=fit, frame_inside_available=self.available.contains(window.frameGeometry()),
                        controls=controls, synthetic=True, evidence_kind='offscreen_widget_render',
                        logical_dpi=screen.logicalDotsPerInch(), alpha_background=alpha_background)
        baseline = self.args.baseline / f'{self.theme}-{BASELINE_STATES.get(state, state)}.png'
        metadata['baseline_image'] = str(baseline.resolve()) if baseline.is_file() else None
        metadata['baseline_relation'] = ('same_state' if state not in BASELINE_STATES else 'closest_prior_surface') if baseline.is_file() else 'not_available'
        metadata_path = self.args.output / f'{name}.json'
        encoded_metadata = json.dumps(metadata, ensure_ascii=False, indent=2)
        metadata_path.write_text(encoded_metadata, encoding='utf-8')
        legacy.with_suffix('.json').write_text(encoded_metadata, encoding='utf-8')
        self.frames.append(metadata)

    def capture_table_menu(self, table):
        """Render the real command menu without choosing an action or blocking."""
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtWidgets import QMenu
        from PySide6.QtCore import QPoint
        from PySide6.QtCore import QTimer
        shown = []
        def capture_popup():
            menu = self.app.activePopupWidget()
            if not isinstance(menu, QMenu):
                raise RuntimeError('Table command menu was not created')
            shown.append(menu)
            try:
                self.register(menu)
                self.capture(menu, 'table-commands-menu', fit=False)
            finally:
                menu.close()
        point = table.visualItemRect(table.currentItem()).center() if table.currentItem() else QPoint(20, 20)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, table.viewport().mapToGlobal(point))
        # The real production menu uses exec(). Capture it from that event loop
        # and close without triggering any action; no generated duplicate menu.
        QTimer.singleShot(0, capture_popup)
        table.contextMenuEvent(event)
        if not shown:
            raise RuntimeError('Table command menu was not captured')

    def finish(self):
        for module, original in self._original_fits:
            module.fit_window_to_screen = original
        self._hardware.memory_capacity = self._original_memory
        if self.args.original_scale is None:
            os.environ.pop('QT_SCALE_FACTOR', None)
        else:
            os.environ['QT_SCALE_FACTOR'] = self.args.original_scale
        unmatched = [pattern for pattern in self.args.states or [] if not any(fnmatch.fnmatchcase(state, pattern) for state in self.encountered)]
        manifest = dict(scale=self.args.scale, mode=self.mode,
                        available_geometry=rectangle(self.available),
                        rendered_states=sorted({frame['state'] for frame in self.frames}),
                        available_states=sorted(self.encountered), frames=self.frames, unmatched_filters=unmatched,
                        evidence_kind='offscreen_widget_render')
        path = self.args.output / f'manifest-{self.scale_name}-{self.mode}.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        if unmatched:
            raise ValueError('Unknown state filters: ' + ', '.join(unmatched))
        return path
