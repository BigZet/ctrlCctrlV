//! The Tauri application: shared state, commands for the web UI and the
//! actions behind the tray menu and global hotkeys.
mod history;
mod hotkeys;
mod library;
mod ocr;
mod overlay;
mod preferences;
mod tray;
mod typing;

use std::sync::{Arc, Mutex, MutexGuard, RwLock};

use base64::Engine as _;
use ctrlcctrlv_core::history::{Entry, EntryRef, History};
use ctrlcctrlv_core::models::ModelRegistry;
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_core::remote_catalog::{self, VisionModel};
use ctrlcctrlv_core::review::{self, Change};
use ctrlcctrlv_core::runtime::{LocalRuntime, Session};
use ctrlcctrlv_core::settings::{Settings, data_directory};
use ctrlcctrlv_core::ui_state::UiState;
use ctrlcctrlv_platform::typing::CancelToken;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};

use crate::model::{Phase, RunView, SessionView, Status, events};

pub use hotkeys::Action;

/// What owns the app right now, with the handle that stops it.
enum Activity {
    Idle,
    Selecting,
    Recognizing(Operation),
    Typing(CancelToken),
}

impl Activity {
    fn phase(&self) -> Phase {
        match self {
            Activity::Idle => Phase::Idle,
            Activity::Selecting => Phase::Selecting,
            Activity::Recognizing(_) => Phase::Recognizing,
            Activity::Typing(_) => Phase::Typing,
        }
    }
}

/// The capture in the result window. The image stays in memory (never on
/// disk) so further models can re-check it and their answers can be compared.
pub struct Capture {
    pub id: u64,
    pub png: Option<Arc<Vec<u8>>>,
    pub entry: Option<EntryRef>,
    pub runs: Vec<RunView>,
    pub rounds: u32,
}

impl Capture {
    fn view(&self) -> SessionView {
        let image = self.png.as_ref().map(|png| {
            format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png.as_slice()))
        });
        let text = self.runs.iter().rev().find(|run| !run.text.is_empty()).map(|run| run.text.clone());
        SessionView { capture: self.id, image, text: text.unwrap_or_default(), runs: self.runs.clone() }
    }
}

pub struct Shell {
    settings: RwLock<Settings>,
    pub models: ModelRegistry,
    pub history: Option<History>,
    pub runtime: tokio::sync::Mutex<LocalRuntime>,
    pub ui: Mutex<UiState>,
    pub catalog: Mutex<Vec<VisionModel>>,
    activity: Mutex<Activity>,
    status: Mutex<Status>,
    pub capture: Mutex<Option<Capture>>,
    /// The last deleted history entry and whether it was pinned, for undo.
    pub deleted: Mutex<Option<(Entry, bool)>>,
    pub install: Mutex<Option<tokio_util::sync::CancellationToken>>,
}

pub fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl Shell {
    fn new() -> Result<Self, String> {
        let directory = data_directory();
        let models = ModelRegistry::load(&directory);
        let settings = Settings::load(&directory, &models);
        let runtime = LocalRuntime::new(&directory, Session::Ocr, 1, settings.acceleration(), models.clone())
            .map_err(|e| e.to_string())?;
        // History failures never block OCR or typing.
        let history = History::open(&directory).ok();
        Ok(Self {
            settings: RwLock::new(settings),
            models,
            history,
            runtime: tokio::sync::Mutex::new(runtime),
            ui: Mutex::new(UiState::load(&directory)),
            catalog: Mutex::new(remote_catalog::load_cache(&directory)),
            activity: Mutex::new(Activity::Idle),
            status: Mutex::new(Status::new(Phase::Idle, "Готово")),
            capture: Mutex::new(None),
            deleted: Mutex::new(None),
            install: Mutex::new(None),
        })
    }

    pub fn settings(&self) -> Settings {
        self.settings.read().unwrap_or_else(|poisoned| poisoned.into_inner()).clone()
    }

    fn set_settings(&self, settings: Settings) {
        *self.settings.write().unwrap_or_else(|poisoned| poisoned.into_inner()) = settings;
    }

    fn phase(&self) -> Phase {
        lock(&self.activity).phase()
    }

    /// Moves from idle to `next`; refuses while anything else runs.
    fn begin(&self, next: Activity) -> bool {
        let mut activity = lock(&self.activity);
        if !matches!(*activity, Activity::Idle) {
            return false;
        }
        *activity = next;
        true
    }

    /// Replaces the current activity only while it is in `phase`.
    fn advance(&self, phase: Phase, next: Activity) -> bool {
        let mut activity = lock(&self.activity);
        if activity.phase() != phase {
            return false;
        }
        *activity = next;
        true
    }

    fn finish(&self) {
        *lock(&self.activity) = Activity::Idle;
    }

    /// A display name for a model: the catalog label, or the identifier.
    pub fn label(&self, provider: &str, model: &str) -> String {
        if provider == "local" {
            return self.models.get(model).map_or_else(|| model.to_string(), |m| m.label.clone());
        }
        lock(&self.catalog).iter().find(|m| m.id == model).map_or_else(|| model.to_string(), |m| m.name.clone())
    }
}

pub fn report(app: &AppHandle, status: Status) {
    *lock(&app.state::<Shell>().status) = status.clone();
    tray::show_status(app, &status);
    let _ = app.emit(events::STATUS, status);
}

/// Tells every page that models, the engine or settings changed.
pub fn changed(app: &AppHandle) {
    let _ = app.emit(events::CHANGED, ());
}

/// Shown when an action arrives while another one runs.
fn busy(app: &AppHandle) {
    let shell = app.state::<Shell>();
    report(app, Status::error(shell.phase(), busy_message(shell.phase())));
}

fn busy_message(phase: Phase) -> &'static str {
    match phase {
        Phase::Typing => "Идёт печать · остановите её сочетанием отмены",
        _ => "Сначала завершите или отмените текущую операцию",
    }
}

pub async fn dispatch(app: AppHandle, action: Action) {
    match action {
        Action::Capture => overlay::begin(&app).await,
        Action::Type => typing::start(&app, None),
        Action::TypeDelayed => typing::start(&app, Some(typing::MENU_DELAY)),
        Action::Cancel => cancel(&app),
        Action::ShowWindow => show_main(&app, true),
        Action::Quit => quit(&app).await,
    }
}

fn cancel(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let mut activity = lock(&shell.activity);
    match &*activity {
        Activity::Idle => {}
        Activity::Selecting => {
            *activity = Activity::Idle;
            drop(activity);
            overlay::hide_all(app);
            report(app, Status::new(Phase::Idle, "Выделение отменено"));
        }
        // The owners report the outcome and return to idle themselves.
        Activity::Recognizing(operation) => operation.cancel(),
        Activity::Typing(token) => token.cancel(),
    }
}

fn show_main(app: &AppHandle, focus: bool) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        if focus {
            let _ = window.set_focus();
        }
    }
}

async fn quit(app: &AppHandle) {
    cancel(app);
    if let Some(token) = lock(&app.state::<Shell>().install).as_ref() {
        token.cancel();
    }
    let _ = app.state::<Shell>().runtime.lock().await.stop().await;
    app.exit(0);
}

#[derive(Serialize)]
struct Snapshot {
    status: Status,
    session: Option<SessionView>,
    hotkeys: [String; 3],
}

#[tauri::command]
fn snapshot(app: AppHandle) -> Snapshot {
    let shell = app.state::<Shell>();
    let session = lock(&shell.capture).as_ref().map(Capture::view);
    let s = shell.settings();
    Snapshot {
        status: lock(&shell.status).clone(),
        session,
        hotkeys: [s.capture_hotkey.clone(), s.typing_hotkey.clone(), s.cancel_hotkey.clone()],
    }
}

#[tauri::command]
fn run_action(app: AppHandle, action: Action) {
    tauri::async_runtime::spawn(dispatch(app, action));
}

/// Copies text edited in the result view.
#[tauri::command]
fn copy_text(text: String) -> Result<(), String> {
    ctrlcctrlv_platform::clipboard::Clipboard::new()
        .and_then(|mut clipboard| clipboard.set_text(&text))
        .map_err(|e| e.to_string())
}

/// Line changes from `base` to `proposed`.
#[tauri::command]
fn compare(base: String, proposed: String) -> Vec<Change> {
    review::changes(&base, &proposed)
}

/// `base` with the selected changes towards `proposed` applied.
#[tauri::command]
fn apply_changes(base: String, proposed: String, selected: Vec<usize>) -> Result<String, String> {
    review::apply(&base, &review::changes(&base, &proposed), &selected).map_err(|e| e.to_string())
}

/// A `kill` or a logout ends the process without [`RunEvent::Exit`], and the
/// engine would outlive the app; Windows ties it to the process with a job
/// object, so this is for macOS (and the Linux dev host).
#[cfg(unix)]
fn stop_engine_on_signal(handle: &AppHandle) {
    let handle = handle.clone();
    tauri::async_runtime::spawn(async move {
        use tokio::signal::unix::{SignalKind, signal};
        let (Ok(mut terminate), Ok(mut interrupt), Ok(mut hangup)) =
            (signal(SignalKind::terminate()), signal(SignalKind::interrupt()), signal(SignalKind::hangup()))
        else {
            return;
        };
        tokio::select! {
            _ = terminate.recv() => {}
            _ = interrupt.recv() => {}
            _ = hangup.recv() => {}
        }
        handle.exit(0);
    });
}

#[cfg(not(unix))]
fn stop_engine_on_signal(_handle: &AppHandle) {}

pub fn run() {
    let shell = match Shell::new() {
        Ok(shell) => shell,
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(1);
        }
    };
    let app = tauri::Builder::default()
        .manage(shell)
        .invoke_handler(tauri::generate_handler![
            snapshot,
            run_action,
            copy_text,
            compare,
            apply_changes,
            ocr::recheck,
            ocr::use_text,
            overlay::selection_finished,
            overlay::selection_cancelled,
            history::history_list,
            history::history_open,
            history::history_pin,
            history::history_delete,
            history::history_undo,
            library::models_list,
            library::model_install,
            library::model_cancel_install,
            library::model_delete,
            library::engine_unload,
            library::catalog_list,
            library::catalog_refresh,
            library::catalog_favorite,
            preferences::settings_get,
            preferences::settings_save,
            preferences::review_models_set,
            preferences::api_key_save,
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let handle = app.handle().clone();
            tray::create(&handle)?;
            stop_engine_on_signal(&handle);
            let settings = handle.state::<Shell>().settings();
            if let Err(message) = hotkeys::install(&handle, &settings) {
                report(&handle, Status::error(Phase::Idle, message));
                show_main(&handle, false);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the main window hides it; the app lives in the tray.
            if let WindowEvent::CloseRequested { api, .. } = event
                && window.label() == "main"
            {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Tauri application");
    app.run(|app, event| match event {
        RunEvent::ExitRequested { api, code: None, .. } => api.prevent_exit(),
        RunEvent::Exit => {
            // Never leave the engine running after the app is gone.
            let shell = app.state::<Shell>();
            let _ = tauri::async_runtime::block_on(async { shell.runtime.lock().await.stop().await });
        }
        _ => {}
    });
}
