//! The models page: local GGUF models (install, delete, engine status) and the
//! OpenRouter catalog with favourites.
use ctrlcctrlv_core::catalog::PromptMode;
use ctrlcctrlv_core::http::Http;
use ctrlcctrlv_core::remote_catalog::{self, VisionModel};
use ctrlcctrlv_core::runtime::{LocalRuntime, Session};
use ctrlcctrlv_core::settings::data_directory;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio_util::sync::CancellationToken;

use super::{Shell, changed, lock};
use crate::model::{InstallProgress, events};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalModelView {
    id: String,
    label: String,
    size: u64,
    /// Checks a transcription against the image, not only recognizes.
    reviews: bool,
    license: String,
    source: String,
    installed: bool,
    custom: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryView {
    models: Vec<LocalModelView>,
    engine_installed: bool,
    /// The model in the running engine; `None` when nothing runs.
    loaded: Option<String>,
    /// The engine is busy loading a model or answering.
    busy: bool,
    installing: bool,
    acceleration: String,
    errors: Vec<String>,
}

/// A runtime that only inspects and installs files and never starts the
/// engine, so downloads do not hold the engine that serves OCR.
fn installer(shell: &Shell) -> Result<LocalRuntime, String> {
    let settings = shell.settings();
    LocalRuntime::new(&data_directory(), Session::Review, 1, settings.acceleration(), shell.models.clone())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn models_list(app: AppHandle) -> Result<LibraryView, String> {
    let shell = app.state::<Shell>();
    let files = installer(&shell)?;
    let (loaded, busy) = match shell.runtime.try_lock() {
        Ok(mut runtime) => (runtime.loaded().map(|loaded| loaded.model_id.clone()), false),
        Err(_) => (None, true),
    };
    let models = shell
        .models
        .iter()
        .map(|model| LocalModelView {
            id: model.id.clone(),
            label: model.label.clone(),
            size: model.size(),
            reviews: model.mode == PromptMode::Json,
            license: model.license.clone(),
            source: model.source.clone(),
            installed: files.installed(&model.id),
            custom: model.id.starts_with("custom-"),
        })
        .collect();
    Ok(LibraryView {
        models,
        engine_installed: files.binary().is_some(),
        loaded,
        busy,
        installing: lock(&shell.install).is_some(),
        acceleration: shell.settings().acceleration,
        errors: shell.models.errors().to_vec(),
    })
}

/// Downloads the engine (once) and the model in the background, reporting
/// progress as [`InstallProgress`] events.
#[tauri::command]
pub fn model_install(app: AppHandle, id: String) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let label = shell.models.get(&id).ok_or("Неизвестная модель")?.label.clone();
    let token = CancellationToken::new();
    {
        let mut install = lock(&shell.install);
        if install.is_some() {
            return Err("Дождитесь окончания текущей загрузки".into());
        }
        *install = Some(token.clone());
    }
    let mut runtime = installer(&shell)?;
    tauri::async_runtime::spawn(async move {
        let progress = |done: u64, total: u64, finished: bool, error: Option<String>| InstallProgress {
            model: id.clone(),
            label: label.clone(),
            done,
            total,
            finished,
            error,
        };
        let emit = |done: u64, total: u64| {
            let _ = app.emit(events::INSTALL, progress(done, total, false, None));
        };
        let report = |_: &str, done: u64, total: u64| emit(done, total);
        let result = runtime.install(&id, &token, &report).await;
        let shell = app.state::<Shell>();
        *lock(&shell.install) = None;
        let error = match result {
            Ok(()) => None,
            Err(error) if error.is_cancelled() => Some("Загрузка отменена".to_string()),
            Err(error) => Some(error.to_string()),
        };
        let _ = app.emit(events::INSTALL, progress(0, 0, true, error));
        changed(&app);
    });
    Ok(())
}

#[tauri::command]
pub fn model_cancel_install(app: AppHandle) {
    if let Some(token) = lock(&app.state::<Shell>().install).as_ref() {
        token.cancel();
    }
}

#[tauri::command]
pub async fn model_delete(app: AppHandle, id: String) -> Result<(), String> {
    let shell = app.state::<Shell>();
    if lock(&shell.install).is_some() {
        return Err("Дождитесь окончания загрузки".into());
    }
    let result = shell.runtime.lock().await.delete(&id).await.map_err(|e| e.to_string());
    changed(&app);
    result
}

/// Stops the engine and frees its memory; the next local OCR loads it again.
#[tauri::command]
pub async fn engine_unload(app: AppHandle) -> Result<(), String> {
    let result = app.state::<Shell>().runtime.lock().await.stop().await.map_err(|e| e.to_string());
    changed(&app);
    result
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogView {
    models: Vec<VisionModel>,
    favorites: Vec<String>,
}

#[tauri::command]
pub fn catalog_list(app: AppHandle) -> CatalogView {
    let shell = app.state::<Shell>();
    CatalogView { models: lock(&shell.catalog).clone(), favorites: lock(&shell.ui).remote_favorites() }
}

#[tauri::command]
pub async fn catalog_refresh(app: AppHandle) -> Result<CatalogView, String> {
    let models = remote_catalog::fetch_models(Http::shared()).await.map_err(|e| e.to_string())?;
    let _ = remote_catalog::save_cache(&data_directory(), &models);
    *lock(&app.state::<Shell>().catalog) = models;
    Ok(catalog_list(app))
}

#[tauri::command]
pub fn catalog_favorite(app: AppHandle, id: String, favorite: bool) -> Result<Vec<String>, String> {
    lock(&app.state::<Shell>().ui).set_remote_favorite(&id, favorite).map_err(|e| e.to_string())
}
