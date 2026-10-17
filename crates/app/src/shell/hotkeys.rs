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

fn bindings(settings: &Settings) -> [(Action, &str); 3] {
    [
        (Action::Capture, settings.capture_hotkey.as_str()),
        (Action::Type, settings.typing_hotkey.as_str()),
        (Action::Cancel, settings.cancel_hotkey.as_str()),
    ]
}

/// Registers the capture, typing and cancel shortcuts. Call on the main thread.
pub fn install(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    let mut hotkeys = Hotkeys::new().map_err(|e| e.to_string())?;
    let registered = hotkeys.register(&bindings(settings)).map_err(|e| e.to_string());
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
    registered
}

/// Registers the shortcuts from changed settings. Call on the main thread.
pub fn reload(settings: &Settings) -> Result<(), String> {
    HOTKEYS.with(|slot| match slot.borrow_mut().as_mut() {
        Some(hotkeys) => hotkeys.register(&bindings(settings)).map_err(|e| e.to_string()),
        None => Err("Горячие клавиши недоступны".into()),
    })
}
