# ctrlcctrlv-platform

Native layer of the Rust rewrite for Windows x64 and macOS ARM64. It replaces
`windows.py`, `macos.py`, `typing.py`, `hotkeys.py` and the Qt screen grab and
clipboard calls of the Python app.

| Module | Windows | macOS |
|---|---|---|
| `hotkey::Hotkeys` | `RegisterHotKey` via `global-hotkey` | Carbon `RegisterEventHotKey` via `global-hotkey` (no Input Monitoring) |
| `typing` + `NativeKeyboard` | `SendInput` with `KEYEVENTF_UNICODE` | `CGEventPost` with a Unicode string |
| focused window | `GetForegroundWindow` | `NSWorkspace` frontmost pid + `AXFocusedWindow` |
| `permissions` | always granted | `AXIsProcessTrusted`, `CGPreflightScreenCaptureAccess` |
| `capture` | `xcap`, only the selected region | `xcap`, only the selected region |
| `clipboard` | `arboard` | `arboard` |

Typing reads the full window identity once per run and once per line; before
each keystroke it only checks cancellation, modifiers and the cheap focus test
(foreground `HWND` / frontmost pid).

```sh
cargo test                                   # portable logic, any host
cargo run --release --example smoke -- type  # on a real Windows or Mac
```
