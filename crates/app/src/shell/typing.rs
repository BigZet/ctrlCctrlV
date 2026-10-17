//! Typing the clipboard into the focused window on a worker thread. A run that
//! is paused (from the panel, by a focus change or a pressed key) keeps its
//! place and continues from there, in the window focused at that moment.
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use ctrlcctrlv_platform::clipboard::Clipboard;
use ctrlcctrlv_platform::typing::{CancelToken, Keyboard, MODIFIER_PRESSED, type_chars};
use ctrlcctrlv_platform::{Error, NativeKeyboard, Permission, permissions, request_permission};
use serde::Deserialize;
use tauri::{AppHandle, Emitter, Manager};

use super::{Activity, Shell, busy, lock, panel, report};
use crate::model::{Phase, RunEnd, Status, TypingJob, TypingState, TypingView, events, typing_settings};

/// Pause before typing started from the tray menu, or while this app's own
/// window has focus.
pub const MENU_DELAY: Duration = Duration::from_secs(3);
const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);
/// A shortcut this soon after a pressed modifier paused typing is that same key press.
const KEY_PAUSE_GRACE: Duration = Duration::from_millis(800);
const PAUSED: &str = "Пауза";

static NEXT_JOB: AtomicU64 = AtomicU64::new(1);

/// Stops the running typing; `stop` says whether for good or as a pause.
#[derive(Clone, Default)]
pub struct Control {
    token: CancelToken,
    stop: Arc<AtomicBool>,
}

impl Control {
    pub fn pause(&self) {
        self.token.cancel();
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
        self.token.cancel();
    }
}

/// Where typing was asked for, which decides the window it goes to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trigger {
    /// The window focused at the press; typing pauses if focus leaves it.
    Hotkey,
    /// The tray menu: wait so the user can return to the target window.
    Menu,
    /// The panel, which does not take focus: the window focused now.
    Panel,
}

/// The typing shortcut starts typing the clipboard, pauses a running job and
/// continues a paused one while the clipboard still holds its text.
pub fn start(app: &AppHandle, trigger: Trigger) {
    let shell = app.state::<Shell>();
    match shell.phase() {
        Phase::Idle => {}
        Phase::Typing if trigger == Trigger::Hotkey => return pause(app),
        _ => return busy(app),
    }
    if !permissions().accessibility {
        request_permission(Permission::Accessibility);
        return report(
            app,
            Status::error(Phase::Idle, "Разрешите ctrlCctrlV управление компьютером (Универсальный доступ)"),
        );
    }
    let text = match Clipboard::new().and_then(|mut clipboard| clipboard.text()) {
        Ok(text) => text,
        Err(e) => return report(app, Status::error(Phase::Idle, e.to_string())),
    };
    {
        let slot = lock(&shell.typing);
        if let Some(job) = slot.as_ref().filter(|job| job.continues_with(&text)) {
            // Pressing the shortcut pressed its modifiers first, which paused
            // the run; this is that press, not a request to continue.
            if trigger == Trigger::Hotkey && job.paused_by_key.is_some_and(|at| at.elapsed() < KEY_PAUSE_GRACE) {
                return;
            }
            drop(slot);
            return launch(app, trigger);
        }
    }
    if text.is_empty() {
        return report(app, Status::error(Phase::Idle, "Буфер обмена пуст"));
    }
    discard(app);
    let settings = typing_settings(&shell.settings());
    *lock(&shell.typing) = Some(TypingJob::new(NEXT_JOB.fetch_add(1, Ordering::Relaxed), text, settings));
    launch(app, trigger);
}

/// Types the rest of the job: from its position, after a countdown when
/// the target window may not be focused yet.
fn launch(app: &AppHandle, trigger: Trigger) {
    let shell = app.state::<Shell>();
    let focused = NativeKeyboard.focused_target();
    let own = focused.as_ref().is_none_or(|target| NativeKeyboard.owned_by_this_app(target));
    let (delay, expected) = match trigger {
        Trigger::Menu => (Some(MENU_DELAY), None),
        _ if own => (Some(MENU_DELAY), None),
        Trigger::Hotkey => (None, focused),
        Trigger::Panel => (None, None),
    };
    let control = Control::default();
    if !shell.begin(Activity::Typing(control.clone())) {
        return busy(app);
    }
    let started = lock(&shell.typing).as_mut().map(|job| {
        job.state = if delay.is_some() { TypingState::Countdown } else { TypingState::Typing };
        (job.chars.clone(), job.position, job.settings)
    });
    let Some((chars, position, settings)) = started else {
        shell.finish();
        return;
    };
    panel::show(app);
    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(delay) = delay {
            for left in (1..=delay.as_secs()).rev() {
                update(&app, |job| {
                    job.countdown = left;
                    job.message = format!("Печать через {left} с · переключитесь в нужное окно");
                });
                report(
                    &app,
                    Status::new(Phase::Typing, format!("Печать через {left} с · переключитесь в нужное окно")),
                );
                if control.token.wait(Duration::from_secs(1)) {
                    return finish(&app, &control, position, Err(Error::Cancelled(PAUSED.into())));
                }
            }
        }
        update(&app, |job| {
            job.state = TypingState::Typing;
            job.countdown = 0;
            job.message = "Печатаю…".into();
        });
        report(&app, Status::new(Phase::Typing, "Печатаю…"));
        let mut last = Instant::now();
        let progress = |done: usize, total: usize| {
            if done == total || last.elapsed() >= PROGRESS_INTERVAL {
                last = Instant::now();
                update(&app, |job| job.position = done);
            }
        };
        let (position, result) =
            type_chars(&NativeKeyboard, &chars, position, &settings, &control.token, progress, expected.as_ref());
        finish(&app, &control, position, result);
    });
}

fn finish(app: &AppHandle, control: &Control, position: usize, result: ctrlcctrlv_platform::Result<()>) {
    let shell = app.state::<Shell>();
    shell.finish();
    let by_key = matches!(&result, Err(Error::Cancelled(message)) if message == MODIFIER_PRESSED);
    let end = match result {
        Ok(()) => RunEnd::Completed,
        Err(_) if control.stop.load(Ordering::SeqCst) => RunEnd::Stopped,
        Err(_) if control.token.is_cancelled() => RunEnd::Interrupted { message: PAUSED.into(), error: false },
        Err(Error::Cancelled(message)) => RunEnd::Interrupted { message, error: false },
        Err(Error::Operation(message)) => RunEnd::Interrupted { message, error: true },
    };
    let ended = {
        let mut slot = lock(&shell.typing);
        let Some(job) = slot.as_mut() else { return };
        job.end(position, end);
        if by_key && job.state == TypingState::Paused {
            job.paused_by_key = Some(Instant::now());
        }
        job.clone()
    };
    conclude(app, &ended);
}

/// Tells the panel and the main window how the job ended, and stores a
/// finished job in history.
fn conclude(app: &AppHandle, job: &TypingJob) {
    let shell = app.state::<Shell>();
    emit(app);
    let message = match job.state {
        TypingState::Paused => {
            format!("{} · напечатано {} из {}", job.message, job.position, job.chars.len())
        }
        _ => job.message.clone(),
    };
    report(app, if job.error { Status::error(Phase::Idle, message) } else { Status::new(Phase::Idle, message) });
    if job.state.finished() {
        if let Some(history) = &shell.history {
            let _ = history.add("typing", &job.text, "", job.stored_status());
        }
        panel::hide_later(app, job.id);
    }
}

/// Changes the job and sends it to the panel.
fn update(app: &AppHandle, change: impl FnOnce(&mut TypingJob)) {
    if let Some(job) = lock(&app.state::<Shell>().typing).as_mut() {
        change(job);
    }
    emit(app);
}

pub fn view(app: &AppHandle) -> Option<TypingView> {
    let shell = app.state::<Shell>();
    let hotkey = shell.settings().typing_hotkey;
    lock(&shell.typing).as_ref().map(|job| job.view(&hotkey))
}

fn emit(app: &AppHandle) {
    if let Some(view) = view(app) {
        let _ = app.emit(events::TYPING, view);
    }
}

pub fn pause(app: &AppHandle) {
    if let Activity::Typing(control) = &*lock(&app.state::<Shell>().activity) {
        control.pause();
    }
}

/// Stops typing for good: the running job, or the paused one.
pub fn stop(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if let Activity::Typing(control) = &*lock(&shell.activity) {
        // The worker reports the outcome.
        return control.stop();
    }
    discard(app);
}

/// Ends a paused job without typing the rest.
fn discard(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let ended = {
        let mut slot = lock(&shell.typing);
        match slot.as_mut().filter(|job| job.state == TypingState::Paused) {
            Some(job) => {
                job.end(job.position, RunEnd::Stopped);
                job.clone()
            }
            None => return,
        }
    };
    conclude(app, &ended);
}

pub fn resume(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let paused = lock(&shell.typing).as_ref().is_some_and(|job| job.state == TypingState::Paused);
    if !paused {
        return;
    }
    if shell.phase() != Phase::Idle {
        return busy(app);
    }
    launch(app, Trigger::Panel);
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PanelAction {
    Pause,
    Resume,
    Stop,
}

#[tauri::command]
pub fn typing_state(app: AppHandle) -> Option<TypingView> {
    view(&app)
}

/// Async so the panel window can be shown from it without blocking the event loop.
#[tauri::command]
pub async fn typing_control(app: AppHandle, action: PanelAction) {
    match action {
        PanelAction::Pause => pause(&app),
        PanelAction::Resume => resume(&app),
        PanelAction::Stop => stop(&app),
    }
}
