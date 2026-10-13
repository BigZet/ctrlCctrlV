# Full UI implementation — accepted user plan

Authority: user's full implementation request in this chat, 2026-10-12. Implement all four groups, preserve PySide6/Windows/macOS and the lightweight OCR → clipboard / optional manual refinement workflow. No paid model calls, no commits or overwrites of earlier work.

## Work ledger

- [x] Preview: stable single status; original restoration in menu; image zoom 25–400%, fit/100%, visibility and splitter persistence; checkable view menu; line-linked syntax hints.
- [x] Review: separate Before/After window, synchronized scrolling, line numbers, character differences, selected changes and optional technical diff.
- [x] Tables/save: TSV rectangles and paste expansion/one undo, visual first-row heading, CSV/XLSX actions, save shortcut, last directory, timestamp filenames.
- [x] Models: hide unavailable delete, consistent disabled style, per-row progress/queue, selection counts/download size, downloaded/favorite filters/install link.
- [x] Work/config: compact cards, synchronized quick preview, cloud connection first, speed presets 2/20,8/60,20/120 ms + manual; recorded hotkeys; conditional permission disclosure; pending-only Save.
- [x] Import: link/search first, discovered selection + advanced fields after search; computations auto-scroll.
- [x] Onboarding: actionable local download / cloud key connection instead of empty page.
- [x] History: day groups, empty states, open/save by id; editor without image or refinement; immutable original entries.
- [x] Diagnostics/tray: summary/JSON save; shortcut labels/idle Stop disabled/separator. Completion audit found remaining capture-wording mismatch (OPEN-03 in the Markdown report).
- [x] Capture: optional correction of selection, move/resize/Enter/Esc; default quick release retained.
- [x] UI state: isolated profile file, validated defaults, corrupt state never resets OCR settings.
- [x] Integration: source/model progress identity, errors by action, no timing-target copy, no extra OCR passes.
- [x] Verify: focused tests, full suite, renders light/dark at 100/150/200%, independent review, frozen smoke/build, same-profile restart without losing edits.

## Interfaces and ownership

- Root: `ui_state.py`, `app.py`, integration, later non-Preview `ui.py` sections, theme, final build/restart.
- Preview implementer: `ui.py` Preview/ImageView/CodeTextEdit/LineNumbers only, `csv_editor.py`, new review presentation module, focused tests. Do not modify SetupWizard/SelectionOverlay/DiagnosticsDialog.
- Workspace implementer: `workspace_ui.py`, `model_picker.py`, new hotkey/preset controls, focused tests. Do not modify app.py/ui.py/theme.py.
- Shared preferences: `UiState(directory=None)`, `get(key, default=None)`, `set(**changes) -> bool`. Keys: preview_sizes, image_visible, last_save_directory, favorites, capture_adjust, capture_help_seen. Favorites are `source:identifier` strings.
- Integration entry points to expose: Preview.set_ui_state(state), Preview.display_history(text, model=''); SettingsWindow.set_ui_state(state); ModelPicker.set_ui_state(state), set_installed(identifiers), install_requested(str).
- Model row progress: SettingsWindow.set_model_queue(identifiers), set_model_progress(identifier,label,done,total), clear_model_queue(); root carries model id around existing progress callbacks.
- History signals: open_requested(int), save_requested(int); root resolves entry and uses preview/editor without source image.

## Rulings

- Keep current working directory: accepted plan explicitly preserves substantial uncommitted implementation; copying only HEAD to a worktree would lose required baseline. No reset/clean/commit.
- Subtasks share files only through declared interfaces; root postpones ui.py changes until preview implementer has finished.
- User requested the full implementation, including eventual launch, so no stage-by-stage approval pauses.
- Native macOS verification requires a Mac and will be reported separately; Windows and shared Qt behavior can be checked here.

## Release evidence

433 tests passed (19.13 s). Windows build and frozen smoke passed; both themes rendered at normal/minimum sizes and 100/150/200%. New Windows app PID 49232 is running on the same profile; settings/history hashes unchanged. See `docs/checks/windows-ui-complete.json`. Native macOS validation remains explicitly unperformed. No paid inference calls and no commits.


## Independent completion audit

Three parallel reviewers completed UI/product, platform lifecycle and test/package audits. Fixed reproduced draft-loss, minimum picker geometry, unavailable selection, invisible history errors, Unicode search, cancellation partial-file preservation, fail-closed typing dispatch, and Mac hotkey/window identity issues. 485 tests passed in20.87s; source/frozen and independent extracted-ZIP smoke passed. Final separate package: `artifacts/builds/windows-project-audit/ctrlCctrlV-windows-x64.zip`. PID50548 runs with the same profile; settings/history/UI-state hashes unchanged. See `docs/checks/project-audit-2026-10-12.json` and the prioritized followups in `docs/INTERFACE.md`. Native macOS remains explicitly unverified.
