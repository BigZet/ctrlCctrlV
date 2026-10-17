//! The settings page. Settings live in the Python app's `settings.json` and
//! apply at once: hotkeys are registered again, the tray menu shows them.
use ctrlcctrlv_core::credentials::{Credentials, check_key};
use ctrlcctrlv_core::settings::{Settings, data_directory};
use ctrlcctrlv_core::ui_state::ModelRef;
use serde::Serialize;
use tauri::{AppHandle, Manager};

use super::{Shell, changed, hotkeys, lock, tray};
use crate::model::ModelChoice;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsView {
    settings: Settings,
    has_key: bool,
    /// Checked by default in the result window; the first one is also the
    /// Python app's review model.
    review_models: Vec<ModelChoice>,
}

#[tauri::command]
pub fn settings_get(app: AppHandle) -> SettingsView {
    let shell = app.state::<Shell>();
    let has_key = Credentials::system().and_then(|c| c.read()).is_ok_and(|key| !key.is_empty());
    SettingsView { settings: shell.settings(), has_key, review_models: review_models(&shell) }
}

/// The stored list, or the Python app's single review model before the list
/// was ever saved.
fn review_models(shell: &Shell) -> Vec<ModelChoice> {
    let stored = lock(&shell.ui).review_models();
    if !stored.is_empty() {
        return stored.into_iter().map(|m| ModelChoice { provider: m.provider, model: m.model }).collect();
    }
    let settings = shell.settings();
    let local = settings.review_provider == "local";
    let model = if local { settings.review_local_model } else { settings.review_remote_model };
    vec![ModelChoice { provider: if local { "local" } else { "openrouter" }.into(), model }]
}

/// Saves the default selection of the result window. The first model also
/// becomes the review model in `settings.json`, so the Python app follows.
#[tauri::command]
pub fn review_models_set(app: AppHandle, models: Vec<ModelChoice>) -> Result<Vec<ModelChoice>, String> {
    let shell = app.state::<Shell>();
    let mut seen = std::collections::HashSet::new();
    let models: Vec<ModelChoice> = models.into_iter().filter(|m| seen.insert(m.clone())).collect();
    let refs: Vec<ModelRef> =
        models.iter().map(|m| ModelRef { provider: m.provider.clone(), model: m.model.clone() }).collect();
    lock(&shell.ui).set_review_models(&refs).map_err(|e| e.to_string())?;
    if let Some(first) = models.first() {
        let mut settings = shell.settings();
        settings.review_provider = first.provider.clone();
        if first.local() {
            settings.review_local_model = first.model.clone();
        } else {
            settings.review_remote_model = first.model.clone();
        }
        // A model the Python app would refuse (an OCR-only local one) is kept
        // out of its field; the list itself still holds it.
        if settings.validate(&shell.models).is_ok() {
            settings.save(&data_directory(), &shell.models).map_err(|e| e.to_string())?;
            shell.set_settings(settings);
        }
    }
    changed(&app);
    Ok(models)
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
