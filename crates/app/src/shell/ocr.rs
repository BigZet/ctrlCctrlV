//! OCR of a selected region and re-checks of the same capture by more models.
//! Every answer streams into the result window; the first one also goes to
//! the clipboard, as before.
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use ctrlcctrlv_core::catalog::PromptMode;
use ctrlcctrlv_core::credentials::Credentials;
use ctrlcctrlv_core::history::NewRun;
use ctrlcctrlv_core::ocr::{OcrProvider, OcrResult, Request};
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_core::review::{self, REVIEW_PROMPT};
use ctrlcctrlv_core::settings::Settings;
use ctrlcctrlv_platform::capture::{Region, capture_region, encode_png};
use ctrlcctrlv_platform::clipboard::Clipboard;
use ctrlcctrlv_platform::{Permission, permissions, request_permission};
use tauri::{AppHandle, Emitter, Manager};

use super::{Activity, Capture, Shell, busy_message, changed, lock, report, show_main};
use crate::model::{
    ModelChoice, OPENROUTER_ENDPOINT, Phase, RunKind, RunState, RunView, Status, copied_message, events, split_choices,
};

/// Long enough for the compositor to drop the hidden overlays from the screen.
const OVERLAY_GONE: Duration = Duration::from_millis(80);
const NOT_INSTALLED: &str =
    "Локальная модель не установлена. Установите её в разделе «Модели» или выберите OpenRouter.";

static NEXT_CAPTURE: AtomicU64 = AtomicU64::new(1);
static NEXT_RUN: AtomicU64 = AtomicU64::new(1);

pub fn next_capture() -> u64 {
    NEXT_CAPTURE.fetch_add(1, Ordering::Relaxed)
}

pub fn next_run() -> u64 {
    NEXT_RUN.fetch_add(1, Ordering::Relaxed)
}

fn max_tokens(settings: &Settings) -> u32 {
    settings.max_tokens.clamp(1, u32::MAX as i64) as u32
}

fn remote_key() -> Result<String, String> {
    let key = Credentials::system().and_then(|c| c.read()).map_err(|e| e.to_string())?;
    if key.is_empty() {
        return Err("Введите и сохраните API-ключ OpenRouter в разделе «Модели»".into());
    }
    Ok(key)
}

/// The model the settings pick for the first recognition of a capture.
fn primary(settings: &Settings) -> ModelChoice {
    if settings.provider == "openrouter" {
        ModelChoice { provider: "openrouter".into(), model: settings.remote_model.clone() }
    } else {
        ModelChoice { provider: "local".into(), model: settings.local_model.clone() }
    }
}

/// Checks before the overlay opens that a capture can be recognized.
pub async fn preflight(shell: &Shell) -> Result<(), String> {
    if !permissions().screen_capture {
        request_permission(Permission::ScreenCapture);
        return Err("Разрешите ctrlCctrlV запись экрана в системных настройках".into());
    }
    let settings = shell.settings();
    if settings.provider == "openrouter" {
        return remote_key().map(drop);
    }
    // A locked runtime is loading a model already.
    let Ok(mut runtime) = shell.runtime.try_lock() else { return Ok(()) };
    let id = &settings.local_model;
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
        let settings = shell.settings();
        if settings.provider == "openrouter" {
            return;
        }
        let id = settings.local_model;
        let mut runtime = shell.runtime.lock().await;
        if runtime.provider(&id, 1).is_none() && runtime.installed(&id) {
            // A failure is reported by `recognize`, which tries again.
            let _ = runtime.start(&id, Operation::new().token(), &|_, _, _| {}).await;
        }
    });
}

/// A ready provider for `choice`; local models are loaded into the engine first.
async fn provider(
    app: &AppHandle,
    shell: &Shell,
    operation: &Operation,
    choice: &ModelChoice,
    kind: RunKind,
) -> Result<OcrProvider, String> {
    let settings = shell.settings();
    if !choice.local() {
        // The protocol picked in settings applies to that model; others use JSON.
        let mode = if kind == RunKind::Ocr && choice.model == settings.remote_model {
            settings.remote_mode()
        } else {
            PromptMode::Json
        };
        let mut provider = OcrProvider::new(OPENROUTER_ENDPOINT, &choice.model, remote_key()?)
            .mode(mode)
            .max_tokens(max_tokens(&settings));
        if let Some(known) = lock(&shell.catalog).iter().find(|m| m.id == choice.model) {
            provider.response_format = known.structured;
        }
        return Ok(provider);
    }
    let id = &choice.model;
    let model = shell.models.get(id).ok_or("Неизвестная локальная модель")?;
    if kind == RunKind::Review && model.mode != PromptMode::Json {
        return Err(format!(
            "{} только распознаёт и не проверяет текст. Выберите режим «Распознать заново».",
            model.label
        ));
    }
    let mut runtime = shell.runtime.lock().await;
    if runtime.provider(id, 1).is_none() {
        if !runtime.installed(id) {
            return Err(NOT_INSTALLED.into());
        }
        report(app, Status::new(Phase::Recognizing, format!("Загружаю {}…", model.label)));
        runtime.start(id, operation.token(), &|_, _, _| {}).await.map_err(|e| e.to_string())?;
        changed(app);
    }
    runtime.provider(id, max_tokens(&settings)).ok_or_else(|| "Локальная модель не запустилась".into())
}

fn new_run(
    shell: &Shell,
    capture: u64,
    round: u32,
    kind: RunKind,
    choice: &ModelChoice,
    base: Option<&str>,
) -> RunView {
    RunView {
        id: next_run(),
        capture,
        round,
        kind,
        provider: choice.provider.clone(),
        model: choice.model.clone(),
        label: shell.label(&choice.provider, &choice.model),
        state: RunState::Waiting,
        text: String::new(),
        base: base.map(str::to_string),
        error: None,
        elapsed_ms: 0,
    }
}

/// Stores the run in the capture and sends it to the result window.
fn publish(app: &AppHandle, run: &RunView) {
    let shell = app.state::<Shell>();
    if let Some(capture) = lock(&shell.capture).as_mut().filter(|c| c.id == run.capture)
        && let Some(slot) = capture.runs.iter_mut().find(|r| r.id == run.id)
    {
        *slot = run.clone();
    }
    let _ = app.emit(events::RUN, run);
}

/// Runs one model on the capture, streaming its answer into `run`.
async fn execute(app: &AppHandle, operation: &Operation, png: &[u8], mut run: RunView) -> (RunView, Option<OcrResult>) {
    let shell = app.state::<Shell>();
    let choice = ModelChoice { provider: run.provider.clone(), model: run.model.clone() };
    let outcome = async {
        let provider = provider(app, &shell, operation, &choice, run.kind).await?;
        run.state = RunState::Running;
        publish(app, &run);
        let context = match run.kind {
            RunKind::Review => {
                Some(review::context(run.base.as_deref().unwrap_or_default(), "").map_err(|e| e.to_string())?)
            }
            RunKind::Ocr => None,
        };
        let request = match run.kind {
            RunKind::Review => Request { prompt: Some(REVIEW_PROMPT), context: context.as_ref() },
            RunKind::Ocr => Request::default(),
        };
        let streaming = run.clone();
        let partial = move |text: &str| {
            let mut update = streaming.clone();
            update.text = text.to_string();
            let _ = app.emit(events::RUN, update);
        };
        let started = Instant::now();
        let result = provider.recognize_stream(png, operation, &request, &partial).await.map_err(|e| e.to_string());
        run.elapsed_ms = started.elapsed().as_millis().min(u64::MAX as u128) as u64;
        result
    }
    .await;
    let result = match outcome {
        Ok(result) if run.kind == RunKind::Review && result.text.trim().is_empty() => {
            Err("Модель проверки вернула пустой текст".to_string())
        }
        other => other,
    };
    let result = match result {
        _ if operation.is_cancelled() => {
            run.state = RunState::Cancelled;
            None
        }
        Ok(result) => {
            run.state = RunState::Done;
            run.text = result.text.clone();
            Some(result)
        }
        Err(message) => {
            run.state = RunState::Failed;
            run.error = Some(message);
            None
        }
    };
    if let Some(result) = &result {
        record(&shell, &run, result);
    }
    publish(app, &run);
    (run, result)
}

/// Stores a finished answer in history: the first one creates the entry (as
/// the Python app does), every answer is kept as a run of it.
fn record(shell: &Shell, run: &RunView, result: &OcrResult) {
    let Some(history) = &shell.history else { return };
    let mut slot = lock(&shell.capture);
    let Some(capture) = slot.as_mut().filter(|c| c.id == run.capture) else { return };
    if capture.entry.is_none() {
        capture.entry = history
            .add("ocr", &result.text, &run.model, "completed")
            .ok()
            .flatten()
            .and_then(|id| history.get(id).ok().flatten())
            .map(|entry| entry.reference());
    }
    if let Some(entry) = &capture.entry {
        let new = NewRun {
            provider: &run.provider,
            model: &run.model,
            text: &result.text,
            raw_text: &result.raw_text,
            status: "completed",
            elapsed_ms: Some(run.elapsed_ms.min(i64::MAX as u64) as i64),
        };
        let _ = history.add_run(entry, new);
    }
}

pub async fn recognize(app: AppHandle, operation: Operation, monitor: u32, region: Region) {
    let shell = app.state::<Shell>();
    report(&app, Status::new(Phase::Recognizing, "Распознаю…"));
    let outcome = first_run(&app, &shell, &operation, monitor, region).await;
    shell.finish();
    let status = match outcome {
        _ if operation.is_cancelled() => Status::new(Phase::Idle, "Распознавание отменено · буфер не изменён"),
        Ok(status) => status,
        Err(message) => Status::error(Phase::Idle, message),
    };
    report(&app, status);
}

async fn first_run(
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
    let png = Arc::new(png);
    let settings = shell.settings();
    let id = next_capture();
    let run = new_run(shell, id, 1, RunKind::Ocr, &primary(&settings), None);
    let capture = Capture { id, png: Some(png.clone()), entry: None, runs: vec![run.clone()], rounds: 1 };
    let view = capture.view();
    *lock(&shell.capture) = Some(capture);
    let _ = app.emit(events::SESSION, view);
    if settings.show_preview {
        show_main(app, false);
    }
    let (run, result) = execute(app, operation, &png, run).await;
    let Some(result) = result else {
        return Err(run.error.unwrap_or_else(|| "Распознавание не удалось".into()));
    };
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
    Ok(Status::new(Phase::Idle, copied_message(&result.text, Duration::from_millis(run.elapsed_ms))))
}

/// Runs more models on the capture in the result window: cloud ones at once,
/// local ones in turn. A review checks `text` (the edited result) against the
/// image; the window stays open for further rounds.
#[tauri::command]
pub fn recheck(
    app: AppHandle,
    capture: u64,
    models: Vec<ModelChoice>,
    kind: RunKind,
    text: String,
) -> Result<(), String> {
    let shell = app.state::<Shell>();
    if models.is_empty() {
        return Err("Выберите хотя бы одну модель".into());
    }
    if kind == RunKind::Review && text.trim().is_empty() {
        return Err("Нечего проверять: текст пуст".into());
    }
    let (cloud, local) = split_choices(&models);
    let operation = Operation::new();
    let (png, runs) = {
        let mut slot = lock(&shell.capture);
        let current = slot.as_mut().filter(|c| c.id == capture).ok_or("Этот снимок уже закрыт")?;
        let png = current.png.clone().ok_or("Для записи из истории снимок не сохраняется")?;
        if !shell.begin(Activity::Recognizing(operation.clone())) {
            return Err(busy_message(shell.phase()).into());
        }
        current.rounds += 1;
        let base = (kind == RunKind::Review).then_some(text.as_str());
        let runs: Vec<RunView> = cloud
            .iter()
            .chain(&local)
            .map(|choice| new_run(&shell, capture, current.rounds, kind, choice, base))
            .collect();
        current.runs.extend(runs.iter().cloned());
        (png, runs)
    };
    for run in &runs {
        let _ = app.emit(events::RUN, run);
    }
    let total = runs.len();
    report(&app, Status::new(Phase::Recognizing, format!("Проверяю {total} моделями…")));
    tauri::async_runtime::spawn(async move {
        let (cloud_runs, local_runs) = runs.split_at(cloud.len());
        let together =
            futures_util::future::join_all(cloud_runs.iter().map(|run| execute(&app, &operation, &png, run.clone())));
        let in_turn = async {
            let mut finished = Vec::new();
            for run in local_runs {
                finished.push(execute(&app, &operation, &png, run.clone()).await);
            }
            finished
        };
        let (mut finished, rest) = futures_util::join!(together, in_turn);
        finished.extend(rest);
        let shell = app.state::<Shell>();
        shell.finish();
        let done = finished.iter().filter(|(run, _)| run.state == RunState::Done).count();
        let status = if operation.is_cancelled() {
            Status::new(Phase::Idle, "Проверка остановлена")
        } else if done == total {
            Status::new(Phase::Idle, format!("Готово: ответили {done} из {total}"))
        } else {
            Status::error(Phase::Idle, format!("Ответили {done} из {total}; остальные с ошибкой"))
        };
        report(&app, status);
    });
    Ok(())
}

/// Makes `text` the capture's result: copies it and stores it as the history
/// entry's text, so history shows what the user settled on.
#[tauri::command]
pub fn use_text(app: AppHandle, capture: u64, text: String, model: String) -> Result<(), String> {
    super::copy_text(text.clone())?;
    let shell = app.state::<Shell>();
    let slot = lock(&shell.capture);
    if let (Some(history), Some(entry)) =
        (&shell.history, slot.as_ref().filter(|c| c.id == capture).and_then(|c| c.entry.as_ref()))
    {
        history.update_text(entry, &text, &model).map_err(|e| e.to_string())?;
    }
    Ok(())
}
