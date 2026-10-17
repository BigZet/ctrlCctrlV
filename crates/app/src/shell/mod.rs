//! The Tauri application: shared state, commands for the web UI and the
//! actions behind the tray menu and global hotkeys.
mod hotkeys;
mod ocr;
mod overlay;
mod tray;
mod typing;

use std::sync::{Arc, Mutex, MutexGuard};

use ctrlcctrlv_core::history::{EntryRef, History};
use ctrlcctrlv_core::models::ModelRegistry;
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_core::runtime::{LocalRuntime, Session};
use ctrlcctrlv_core::settings::{Settings, data_directory};
use ctrlcctrlv_platform::typing::CancelToken;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};

use crate::model::{Phase, RunView, Status, events};

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

/// A recognized region. The image stays in memory (never on disk) so further
/// models can re-check the same capture and their answers can be compared.
#[allow(dead_code)] // Read by the multi-model re-check, the next UI step.
pub struct Capture {
    pub id: u64,
    pub png: Arc<Vec<u8>>,
    pub entry: Option<EntryRef>,
    pub runs: Vec<RunView>,
}

pub struct Shell {
    pub settings: Settings,
    pub history: Option<History>,
    pub runtime: tokio::sync::Mutex<LocalRuntime>,
    activity: Mutex<Activity>,
    status: Mutex<Status>,
    pub last: Mutex<Option<Capture>>,
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl Shell {
    fn new() -> Result<Self, String> {
        let directory = data_directory();
        let models = ModelRegistry::load(&directory);
        let settings = Settings::load(&directory, &models);
        let runtime = LocalRuntime::new(&directory, Session::Ocr, 1, settings.acceleration(), models)
            .map_err(|e| e.to_string())?;
        // History failures never block OCR or typing.
        let history = History::open(&directory).ok();
        Ok(Self {
            settings,
            history,
            runtime: tokio::sync::Mutex::new(runtime),
            activity: Mutex::new(Activity::Idle),
            status: Mutex::new(Status::new(Phase::Idle, "Готово")),
            last: Mutex::new(None),
        })
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
}

pub fn report(app: &AppHandle, status: Status) {
    *lock(&app.state::<Shell>().status) = status.clone();
    tray::show_status(app, &status);
    let _ = app.emit(events::STATUS, status);
}

/// Shown when an action arrives while another one runs.
fn busy(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let message = match shell.phase() {
        Phase::Typing => "Идёт печать · остановите её сочетанием отмены",
        _ => "Сначала завершите или отмените текущую операцию",
    };
    report(app, Status::error(shell.phase(), message));
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
    let _ = app.state::<Shell>().runtime.lock().await.stop().await;
    app.exit(0);
}

#[derive(Serialize)]
struct Snapshot {
    status: Status,
    runs: Vec<RunView>,
    hotkeys: [String; 3],
}

#[tauri::command]
fn snapshot(app: AppHandle) -> Snapshot {
    let shell = app.state::<Shell>();
    let runs = lock(&shell.last).as_ref().map(|c| c.runs.clone()).unwrap_or_default();
    let s = &shell.settings;
    Snapshot {
        status: lock(&shell.status).clone(),
        runs,
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
            overlay::selection_finished,
            overlay::selection_cancelled,
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let handle = app.handle().clone();
            tray::create(&handle)?;
            let settings = handle.state::<Shell>().settings.clone();
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
