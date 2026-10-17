# ctrlcctrlv-app

The Tauri 2 desktop shell. Targets: Windows x64 and macOS ARM64; on other
hosts only the portable logic in `src/model.rs` and its tests are built.

| Piece | Where |
|---|---|
| Tray icon and menu | `src/shell/tray.rs` |
| Global hotkeys from `settings.json` (capture, type, cancel) | `src/shell/hotkeys.rs` |
| Region selection: a transparent topmost window per monitor | `src/shell/overlay.rs`, `ui/overlay.*` |
| Capture → OCR (local engine or OpenRouter) → clipboard → history | `src/shell/ocr.rs` |
| Typing the clipboard into the focused window | `src/shell/typing.rs` |
| Main window: status, last answer, copy again | `ui/index.html`, `ui/main.*` |

Settings, history, models and the OpenRouter key are shared with the Python
app (same data directory), so a model installed there is used here.

The Rust side talks to the web UI only through the events and commands named
in `src/model.rs`. A capture keeps its PNG in memory and every answer is a
`RunView` tagged with its capture id, so re-checks with more models,
streaming output and a typing panel can be added without changing the flow.

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
