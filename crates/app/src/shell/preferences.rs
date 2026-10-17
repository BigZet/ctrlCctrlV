//! The settings page. Settings live in the Python app's `settings.json` and
//! apply at once: hotkeys are registered again, the tray menu shows them.
use ctrlcctrlv_core::credentials::{Credentials, check_key};
use ctrlcctrlv_core::settings::{Settings, data_directory};
use serde::Serialize;
use tauri::{AppHandle, Manager};

use super::{Shell, changed, hotkeys, tray};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsView {
    settings: Settings,
    has_key: bool,
}

#[tauri::command]
pub fn settings_get(app: AppHandle) -> SettingsView {
    let has_key = Credentials::system().and_then(|c| c.read()).is_ok_and(|key| !key.is_empty());
    SettingsView { settings: app.state::<Shell>().settings(), has_key }
}

#[tauri::command]
pub async fn settings_save(app: AppHandle, settings: Settings) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let previous = shell.settings();
    settings.save(&data_directory(), &shell.models).map_err(|e| e.to_string())?;
    shell.set_settings(settings.clone());
    if previous.acceleration != settings.acceleration {
        // The engine for the other acceleration lives in another directory.
        let mut runtime = shell.runtime.lock().await;
        let _ = runtime.stop().await;
        runtime.acceleration = settings.acceleration();
    }
    let hotkeys_changed = (&previous.capture_hotkey, &previous.typing_hotkey, &previous.cancel_hotkey)
        != (&settings.capture_hotkey, &settings.typing_hotkey, &settings.cancel_hotkey);
    let result = if hotkeys_changed {
        // Hotkeys belong to the event-loop thread.
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let next = settings.clone();
        app.run_on_main_thread(move || {
            let _ = sender.send(hotkeys::reload(&next));
        })
        .map_err(|e| e.to_string())?;
        receiver.await.unwrap_or_else(|_| Err("Не удалось применить горячие клавиши".into()))
    } else {
        Ok(())
    };
    let _ = tray::refresh(&app);
    changed(&app);
    result.map_err(|message| format!("Настройки сохранены, но {}", lowercase_first(&message)))
}

fn lowercase_first(text: &str) -> String {
    let mut chars = text.chars();
    chars.next().map(|c| c.to_lowercase().chain(chars).collect()).unwrap_or_default()
}

/// Stores the OpenRouter key in the system credential store; empty removes it.
#[tauri::command]
pub fn api_key_save(app: AppHandle, key: String) -> Result<(), String> {
    let key = key.trim();
    if !key.is_empty() {
        check_key(key).map_err(|e| e.to_string())?;
    }
    let result = Credentials::system().and_then(|c| c.write(key)).map_err(|e| e.to_string());
    changed(&app);
    result
}
