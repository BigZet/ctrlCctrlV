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

/// Registers the capture, typing and cancel shortcuts. Call on the main thread.
pub fn install(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    let mut hotkeys = Hotkeys::new().map_err(|e| e.to_string())?;
    hotkeys
        .register(&[
            (Action::Capture, settings.capture_hotkey.as_str()),
            (Action::Type, settings.typing_hotkey.as_str()),
            (Action::Cancel, settings.cancel_hotkey.as_str()),
        ])
        .map_err(|e| e.to_string())?;
    HOTKEYS.with(|slot| *slot.borrow_mut() = Some(hotkeys));
    let app = app.clone();
    GlobalHotKeyEvent::set_event_handler(Some(move |event: GlobalHotKeyEvent| {
        let action = HOTKEYS.with(|slot| slot.borrow().as_ref().and_then(|hotkeys| hotkeys.action(&event)));
        if let Some(action) = action {
            // Leave the event loop at once; actions call back into it.
            tauri::async_runtime::spawn(super::dispatch(app.clone(), action));
        }
    }));
    Ok(())
}
