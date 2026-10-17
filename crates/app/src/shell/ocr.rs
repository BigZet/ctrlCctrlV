//! OCR of a selected region: capture, recognize, copy to the clipboard, record.
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use ctrlcctrlv_core::credentials::Credentials;
use ctrlcctrlv_core::history::NewRun;
use ctrlcctrlv_core::ocr::{OcrProvider, OcrResult, Request};
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_platform::capture::{Region, capture_region, encode_png};
use ctrlcctrlv_platform::clipboard::Clipboard;
use ctrlcctrlv_platform::{Permission, permissions, request_permission};
use tauri::{AppHandle, Emitter, Manager};

use super::{Capture, Shell, lock, report, show_main};
use crate::model::{OPENROUTER_ENDPOINT, Phase, RunView, Status, copied_message, events};

/// Long enough for the compositor to drop the hidden overlays from the screen.
const OVERLAY_GONE: Duration = Duration::from_millis(80);
const NOT_INSTALLED: &str =
    "Локальная модель не установлена. Установите её в разделе «Модели» или выберите OpenRouter.";

static NEXT_CAPTURE: AtomicU64 = AtomicU64::new(1);

fn remote(shell: &Shell) -> bool {
    shell.settings.provider == "openrouter"
}

fn max_tokens(shell: &Shell) -> u32 {
    shell.settings.max_tokens.clamp(1, u32::MAX as i64) as u32
}

fn remote_key() -> Result<String, String> {
    let key = Credentials::system().and_then(|c| c.read()).map_err(|e| e.to_string())?;
    if key.is_empty() {
        return Err("Введите и сохраните API-ключ OpenRouter в настройках".into());
    }
    Ok(key)
}

/// Checks before the overlay opens that a capture can be recognized.
pub async fn preflight(shell: &Shell) -> Result<(), String> {
    if !permissions().screen_capture {
        request_permission(Permission::ScreenCapture);
        return Err("Разрешите ctrlCctrlV запись экрана в системных настройках".into());
    }
    if remote(shell) {
        return remote_key().map(drop);
    }
    // A locked runtime is loading a model already.
    let Ok(mut runtime) = shell.runtime.try_lock() else { return Ok(()) };
    let id = &shell.settings.local_model;
    if runtime.provider(id, 1).is_some() {
        return Ok(());
    }
    if runtime.binary().is_none() || !runtime.installed(id) {
        return Err(NOT_INSTALLED.into());
    }
    Ok(())
}

/// Starts the local engine in the background so it is ready when the selection ends.
pub fn warm_up(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let shell = app.state::<Shell>();
        if remote(&shell) {
            return;
        }
        let id = shell.settings.local_model.clone();
        let mut runtime = shell.runtime.lock().await;
        if runtime.provider(&id, 1).is_none() && runtime.installed(&id) {
            // A failure is reported by `recognize`, which tries again.
            let _ = runtime.start(&id, Operation::new().token(), &|_, _, _| {}).await;
        }
    });
}

async fn provider(
    app: &AppHandle,
    shell: &Shell,
    operation: &Operation,
) -> Result<(OcrProvider, &'static str), String> {
    let settings = &shell.settings;
    if remote(shell) {
        let provider = OcrProvider::new(OPENROUTER_ENDPOINT, &settings.remote_model, remote_key()?)
            .mode(settings.remote_mode())
            .max_tokens(max_tokens(shell));
        return Ok((provider, "openrouter"));
    }
    let id = &settings.local_model;
    let mut runtime = shell.runtime.lock().await;
    if runtime.provider(id, 1).is_none() {
        if !runtime.installed(id) {
            return Err(NOT_INSTALLED.into());
        }
        report(app, Status::new(Phase::Recognizing, "Загружаю локальную модель…"));
        runtime.start(id, operation.token(), &|_, _, _| {}).await.map_err(|e| e.to_string())?;
    }
    let provider = runtime.provider(id, max_tokens(shell)).ok_or("Локальная модель не запустилась")?;
    Ok((provider, "local"))
}

pub async fn recognize(app: AppHandle, operation: Operation, monitor: u32, region: Region) {
    let shell = app.state::<Shell>();
    report(&app, Status::new(Phase::Recognizing, "Распознаю…"));
    let outcome = run(&app, &shell, &operation, monitor, region).await;
    shell.finish();
    let status = match outcome {
        _ if operation.is_cancelled() => Status::new(Phase::Idle, "Распознавание отменено · буфер не изменён"),
        Ok(status) => status,
        Err(message) => Status::error(Phase::Idle, message),
    };
    report(&app, status);
}

async fn run(
    app: &AppHandle,
    shell: &Shell,
    operation: &Operation,
    monitor: u32,
    region: Region,
) -> Result<Status, String> {
    tokio::time::sleep(OVERLAY_GONE).await;
    let png = tauri::async_runtime::spawn_blocking(move || {
        capture_region(monitor, region).and_then(|image| encode_png(&image))
    })
    .await
    .map_err(|_| "Не удалось сделать снимок экрана".to_string())?
    .map_err(|e| e.to_string())?;
    let (provider, source) = provider(app, shell, operation).await?;
    let started = Instant::now();
    let result = provider.recognize_result(&png, operation, &Request::default()).await.map_err(|e| e.to_string())?;
    let elapsed = started.elapsed();
    if result.text.trim().is_empty() {
        return Ok(Status::new(Phase::Idle, "Текст не найден · буфер не изменён"));
    }
    // A cancel that raced with the answer still leaves the clipboard alone.
    operation.check().map_err(|e| e.to_string())?;
    let text = result.text.clone();
    tauri::async_runtime::spawn_blocking(move || Clipboard::new().and_then(|mut c| c.set_text(&text)))
        .await
        .map_err(|_| "Буфер обмена недоступен".to_string())?
        .map_err(|e| e.to_string())?;
    let message = copied_message(&result.text, elapsed);
    let view = record(shell, Arc::new(png), source, &provider.model, &result, elapsed);
    let _ = app.emit(events::RESULT, view);
    if shell.settings.show_preview {
        show_main(app, false);
    }
    Ok(Status::new(Phase::Idle, message))
}

/// Stores the answer in history (as the Python app does) and keeps the capture
/// in memory for re-checks by other models.
fn record(
    shell: &Shell,
    png: Arc<Vec<u8>>,
    source: &str,
    model: &str,
    result: &OcrResult,
    elapsed: Duration,
) -> RunView {
    let elapsed_ms = elapsed.as_millis().min(i64::MAX as u128) as i64;
    let entry = shell.history.as_ref().and_then(|history| {
        let id = history.add("ocr", &result.text, model, "completed").ok()??;
        let entry = history.get(id).ok()??.reference();
        let run = NewRun {
            provider: source,
            model,
            text: &result.text,
            raw_text: &result.raw_text,
            status: "completed",
            elapsed_ms: Some(elapsed_ms),
        };
        let _ = history.add_run(&entry, run);
        Some(entry)
    });
    let view = RunView {
        capture: NEXT_CAPTURE.fetch_add(1, Ordering::Relaxed),
        provider: source.into(),
        model: model.into(),
        text: result.text.clone(),
        elapsed_ms: elapsed_ms as u64,
    };
    *lock(&shell.last) = Some(Capture { id: view.capture, png, entry, runs: vec![view.clone()] });
    view
}
