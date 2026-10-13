# Interface rework implementation plan

**Goal:** Make every existing application screen understandable and comfortable, with an external indicator only during OCR inference.

**Architecture:** Retain the Qt controller and runtime contracts. Extract a reusable model chooser, arrange the preview around two clearly named operations, and simplify the workspace, settings, library and history using shared visual rules.

**Tech stack:** Python, PySide6, pytest. No new dependencies.

**Design brief:** The supplied screenshot shows an unreadable model dropdown with clipped names and prices, cropped source image, floating controls and ambiguous actions. The user explicitly delegated the full redesign while away. Implementation proceeds autonomously; no review/approval wait is needed. Preserve Windows/macOS support, existing user data and uncommitted work. No paid API calls, commits or publishing are required.

## Screen decisions

- Preview: a modest result header; original image fits by default; editor takes most of the space. A single compact editor toolbar labels language explicitly. Advanced display controls live behind an editor-options menu. Repeat OCR is a named strip with a model selector and “Распознать заново”; verification has its own action and context. Copy explicitly closes the window. Existing review diff remains usable.
- Model chooser: a dedicated resizable dialog, search by name/identifier, Local/OpenRouter filter, full names and separate prices. The closed selector displays only a readable model name. Arbitrary search text cannot become a saved model ID.
- Work: clear “Распознать область” and “Напечатать буфер” actions, visible model and result format, direct settings links, concise countdown information. Avoid duplicated idle status.
- Library: retain the requested table and bulk buttons; make all/individual selection unambiguous and names readable. Group runtime settings consistently.
- Settings: label recognition, verification, typing and system responsibilities; keep only applicable fields, useful errors near their action, advanced settings collapsed.
- History: an intentional empty state; searchable entries with readable dates, text detail and only valid actions.
- Setup/import/diagnostics: consistent typography and action labels, clear validation and loading states.
- Indicator: only when an OCR Operation is active and not cancelled. Never for typing, countdown, model download/warm-up, review, diagnostics or selection. A local model warm-up before repeat OCR must not show it.

## Tasks and evidence

- [x] 1. Add failing behavior tests for indicator visibility and model chooser search/selection/cancel. Implement and verify them.
- [x] 2. Implement the model chooser and preview restructuring; preserve text, clipboard and operation isolation. Verify keyboard access and narrow layouts.
- [x] 3. Rework all workspace and supporting screens; render light/dark, minimum size and 150% scaling. Inspect actual image artifacts and repair clipping/overflow.
- [x] 4. Run the complete suite, obtain an independent review, build a fresh Windows package and run frozen smoke checks. Audit each screen and requirement; record actual evidence before completing the goal.

## Current review risks

Long model names and unknown saved IDs; catalog refresh while selector open; source screenshots wider/taller than the viewport; no installed models; busy/cancelled transitions; review panel expanded in a minimum-size window; user-edited text must survive non-destructive UI changes.

## Execution log

Initial evidence: existing table release is present; supplied screenshot confirms the preview chooser is clipped. Current indicator is keyed to any busy operation, contrary to the new requirement.

Final evidence: 280 tests passed in 12.08s, process exit 0. The initial teardown access violation was investigated: surviving top-level Qt windows were left to interpreter shutdown. Explicit session teardown now destroys them while QApplication is alive; diagnostic hook reports zero surviving windows. Native Windows indicator probe passed all five hidden-state checks, OCR visibility, cancellation, elapsed time and foreground preservation.

Independent read-only review found late review reopening after close and invisible repeat-model preparation. Both were fixed with failing-then-passing regression tests. Follow-up found minimization incorrectly cancelling work; spontaneous hide events are now excluded and covered by a regression test.

Visual audit: artifacts/ui-rework-final, artifacts/ui-rework-final-compact, artifacts/ui-rework-final-150. Inspected work, library, recognition/typing/system settings, empty/populated history, preview/context/editor states, chooser/empty search, review, model import, setup, diagnostics and indicator. Fixed dark table checkbox contrast and missing permission glyphs. User data and global provider selections preserved. Windows package built under artifacts/builds/windows-interface-rework and frozen smoke passed; native macOS verification remains unavailable on this host. No paid inference calls.
