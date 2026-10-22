//! Global hotkeys from settings, delivered as app actions.
use std::cell::RefCell;

use ctrlcctrlv_core::settings::Settings;
use ctrlcctrlv_platform::hotkey::Hotkeys;
use global_hotkey::GlobalHotKeyEvent;
use serde::Deserialize;
use tauri::AppHandle;

/// Everything the tray menu, hotkeys and the web UI can ask for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Action {
    Capture,
    /// Recognize one more region and add its text to the result.
    CaptureAppend,
    Type,
    /// Typing started from a menu or button: wait so the user can focus the target.
    TypeDelayed,
    Cancel,
    ShowWindow,
    Quit,
}

thread_local! {
    // The manager must live on the event-loop thread (the main thread on
    // macOS), which is also where the hotkey events are delivered.
    static HOTKEYS: RefCell<Option<Hotkeys<Action>>> = const { RefCell::new(None) };
}

/// The raw-input listener (Windows), used instead of the system registration
/// while `hotkey_mode` is `rawinput`. Shared, since it lives on its own thread.
#[cfg(windows)]
static RAW: std::sync::Mutex<Option<ctrlcctrlv_platform::hotkey::RawHotkeys>> = std::sync::Mutex::new(None);

const ACTIONS: [Action; 3] = [Action::Capture, Action::Type, Action::Cancel];

fn raw_wanted(settings: &Settings) -> bool {
    cfg!(windows) && settings.hotkey_mode == "rawinput"
}

/// Starts or stops the raw-input listener to match the settings. With it
/// running the system registration is released, so the keys reach the
/// window that has focus as well.
#[cfg(windows)]
fn apply_raw(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    use ctrlcctrlv_platform::hotkey::{RawHotkeys, parse_shortcut};
    let mut slot = RAW.lock().unwrap_or_else(|e| e.into_inner());
    if !raw_wanted(settings) {
        *slot = None;
        return Ok(());
    }
    let shortcuts = bindings(settings)
        .iter()
        .map(|(_, value)| parse_shortcut(value))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    match slot.as_ref() {
        Some(raw) => raw.set_shortcuts(shortcuts),
        None => {
            let app = app.clone();
            let raw = RawHotkeys::start(shortcuts, move |index| {
                if let Some(action) = ACTIONS.get(index) {
                    tauri::async_runtime::spawn(super::dispatch(app.clone(), *action));
                }
            })
            .map_err(|e| e.to_string())?;
            *slot = Some(raw);
        }
    }
    Ok(())
}

#[cfg(not(windows))]
fn apply_raw(_: &AppHandle, _: &Settings) -> Result<(), String> {
    Ok(())
}

fn bindings(settings: &Settings) -> [(Action, &str); 3] {
    [
        (ACTIONS[0], settings.capture_hotkey.as_str()),
        (ACTIONS[1], settings.typing_hotkey.as_str()),
        (ACTIONS[2], settings.cancel_hotkey.as_str()),
    ]
}

/// The system registrations: none while the raw-input listener is on.
fn system_bindings(settings: &Settings) -> Vec<(Action, &str)> {
    if raw_wanted(settings) { Vec::new() } else { bindings(settings).to_vec() }
}

/// Registers the capture, typing and cancel shortcuts. Call on the main thread.
pub fn install(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    let mut hotkeys = Hotkeys::new().map_err(|e| e.to_string())?;
    let registered = hotkeys.register(&system_bindings(settings)).map_err(|e| e.to_string());
    // Kept even when registration failed, so changed settings can retry.
    HOTKEYS.with(|slot| *slot.borrow_mut() = Some(hotkeys));
    let app = app.clone();
    GlobalHotKeyEvent::set_event_handler(Some(move |event: GlobalHotKeyEvent| {
        let action = HOTKEYS.with(|slot| slot.borrow().as_ref().and_then(|hotkeys| hotkeys.action(&event)));
        if let Some(action) = action {
            // Leave the event loop at once; actions call back into it.
            tauri::async_runtime::spawn(super::dispatch(app.clone(), action));
        }
    }));
    registered.and_then(|()| apply_raw(app, settings))
}

/// Registers the shortcuts from changed settings. Call on the main thread.
pub fn reload(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    HOTKEYS.with(|slot| match slot.borrow_mut().as_mut() {
        Some(hotkeys) => hotkeys.register(&system_bindings(settings)).map_err(|e| e.to_string()),
        None => Err("Горячие клавиши недоступны".into()),
    })?;
    apply_raw(app, settings)
}
