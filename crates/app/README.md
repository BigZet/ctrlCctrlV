# ctrlcctrlv-app

The Tauri 2 desktop shell. Targets: Windows x64 and macOS ARM64; on other
hosts only the portable logic in `src/model.rs` and its tests are built.

| Piece | Where |
|---|---|
| Tray icon and menu | `src/shell/tray.rs` |
| Global hotkeys from `settings.json` (capture, type, cancel) | `src/shell/hotkeys.rs` |
| Region selection: a transparent topmost window per monitor | `src/shell/overlay.rs`, `ui/overlay.*` |
| Capture → streamed OCR (local engine or OpenRouter) → clipboard → history; re-checks of the same capture by more models | `src/shell/ocr.rs` |
| Typing the clipboard into the focused window | `src/shell/typing.rs` |
| History: search, pins, delete with undo, stored model runs | `src/shell/history.rs`, `ui/history.js` |
| Local models (install, delete, engine) and the OpenRouter catalog | `src/shell/library.rs`, `ui/models.js` |
| Settings and the OpenRouter key | `src/shell/preferences.rs`, `ui/settings.js` |
| Main window shell: navigation, status, toasts | `ui/index.html`, `ui/app.js`, `ui/main.css` |
| Result window: editor, model answers with line changes, side-by-side comparison | `ui/result.js` |

Settings, history, models and the OpenRouter key are shared with the Python
app (same data directory), so a model installed there is used here.

Settings, `ui-state.json` (history pins, favourite OpenRouter models, the
models checked by default in the result window),
`history.sqlite3` and `openrouter-models.json` are the Python app's files.

The Rust side talks to the web UI only through the events and commands named
in `src/model.rs` and `src/shell/*.rs`. A capture keeps its PNG in memory and
every answer is a `RunView` tagged with its capture id and round. In the
result window the user picks models and runs either "recognize again" (image
only) or "check text" (the edited text goes along as the transcription to
correct). Cloud models run in parallel, local ones in turn, since each needs
the engine to itself. Each finished answer is shown as line changes against
the current text (computed in `ctrlcctrlv_core::review`), which can be applied
selectively; every answer is stored as a run of the history entry.

The UI is plain HTML, CSS and JavaScript without a bundler. To look at it in a
browser, load `ui/index.html` with a stub `window.__TAURI__` that answers the
commands.

## Run

```sh
cargo run -p ctrlcctrlv-app
```

A bundle (an `.app` on macOS) is built with the Tauri CLI from this folder:

```sh
npx @tauri-apps/cli@2 build --bundles app   # macOS
npx @tauri-apps/cli@2 build --no-bundle     # Windows: target/release/ctrlcctrlv.exe
```

On macOS grant Screen Recording and Accessibility to the app on first use.
