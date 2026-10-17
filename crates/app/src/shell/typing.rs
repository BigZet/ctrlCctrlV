//! Typing the clipboard into the focused window on a worker thread.
use std::time::{Duration, Instant};

use ctrlcctrlv_platform::clipboard::Clipboard;
use ctrlcctrlv_platform::typing::{CancelToken, Keyboard, type_text};
use ctrlcctrlv_platform::{Error, NativeKeyboard, Permission, permissions, request_permission};
use tauri::{AppHandle, Emitter, Manager};

use super::{Activity, Shell, busy, report};
use crate::model::{Phase, Status, TypingProgress, events, typing_settings};

/// Pause before typing started from the tray menu or a button.
pub const MENU_DELAY: Duration = Duration::from_secs(3);
const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);

/// `delay` is `None` for the hotkey: the window focused at the press is the
/// target, and typing stops if focus leaves it.
pub fn start(app: &AppHandle, delay: Option<Duration>) {
    let shell = app.state::<Shell>();
    if shell.phase() != Phase::Idle {
        return busy(app);
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
    let expected = if delay.is_none() { NativeKeyboard.focused_target() } else { None };
    let token = CancelToken::new();
    if !shell.begin(Activity::Typing(token.clone())) {
        return busy(app);
    }
    let settings = typing_settings(&shell.settings());
    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(delay) = delay {
            report(
                &app,
                Status::new(Phase::Typing, format!("Печать через {} с · переключитесь в нужное окно", delay.as_secs())),
            );
            if token.wait(delay) {
                return finish(&app, &text, Err(Error::Cancelled("Печать отменена".into())));
            }
        }
        report(&app, Status::new(Phase::Typing, "Печатаю…"));
        let mut last = Instant::now();
        let progress = |done: usize, total: usize| {
            if done == total || last.elapsed() >= PROGRESS_INTERVAL {
                last = Instant::now();
                let _ = app.emit(events::TYPING, TypingProgress { done, total });
            }
        };
        let result = type_text(&NativeKeyboard, &text, &settings, &token, progress, expected.as_ref());
        finish(&app, &text, result);
    });
}

fn finish(app: &AppHandle, text: &str, result: ctrlcctrlv_platform::Result<()>) {
    let shell = app.state::<Shell>();
    shell.finish();
    let (status, stored) = match result {
        Ok(()) => (Status::new(Phase::Idle, "Печать завершена"), "completed"),
        Err(Error::Cancelled(message)) => (Status::new(Phase::Idle, message), "cancelled"),
        Err(Error::Operation(message)) => (Status::error(Phase::Idle, message), "error"),
    };
    if let Some(history) = &shell.history {
        let _ = history.add("typing", text, "", stored);
    }
    report(app, status);
}
