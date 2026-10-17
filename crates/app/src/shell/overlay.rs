//! Region selection: one borderless, transparent, topmost window per monitor.
//! The windows are created on first use and reused until monitors change.
use std::sync::Mutex;

use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_platform::capture::{self, Region};
use tauri::{AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, Monitor, PhysicalPosition, PhysicalSize};
use tauri::{WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use super::{Activity, Shell, busy, lock, ocr, report, show_main};
use crate::model::{Phase, Status, desktop_origin, events, match_monitor};

struct Target {
    label: String,
    /// The capture library's id of the monitor this window covers.
    monitor: u32,
    /// The covered monitor in physical desktop pixels.
    bounds: (f64, f64, f64, f64),
}

#[derive(Default)]
struct Overlays {
    generation: u32,
    /// Origin, size and scale of every monitor the windows were made for.
    topology: Vec<(i32, i32, u32, u32, u64)>,
    targets: Vec<Target>,
}

static OVERLAYS: Mutex<Overlays> = Mutex::new(Overlays { generation: 0, topology: Vec::new(), targets: Vec::new() });

fn topology(monitors: &[Monitor]) -> Vec<(i32, i32, u32, u32, u64)> {
    monitors
        .iter()
        .map(|m| (m.position().x, m.position().y, m.size().width, m.size().height, m.scale_factor().to_bits()))
        .collect()
}

/// Starts a selection, unless OCR is not ready or another operation runs.
/// With `append` the region's text goes after the result in the result
/// window; without a capture there it is an ordinary capture.
pub async fn begin(app: &AppHandle, append: bool) {
    let shell = app.state::<Shell>();
    if shell.phase() != Phase::Idle {
        return busy(app);
    }
    if let Err(message) = ocr::preflight(&shell).await {
        report(app, Status::error(Phase::Idle, message));
        return show_main(app, false);
    }
    let append = append && lock(&shell.capture).as_ref().is_some_and(|capture| capture.png.is_some());
    if !shell.begin(Activity::Selecting { append }) {
        return busy(app);
    }
    match show(app) {
        Ok(()) => {
            let message = if append {
                "Выделите область, чтобы добавить её к результату · Esc — отмена"
            } else {
                "Выделите область · Esc — отмена"
            };
            report(app, Status::new(Phase::Selecting, message));
            // Load the local model while the user is still selecting.
            ocr::warm_up(app);
        }
        Err(message) => {
            shell.finish();
            hide_all(app);
            report(app, Status::error(Phase::Idle, message));
        }
    }
}

fn show(app: &AppHandle) -> Result<(), String> {
    let failed = |e: tauri::Error| format!("Не удалось показать выделение: {e}");
    let monitors = app.available_monitors().map_err(failed)?;
    if monitors.is_empty() {
        return Err("Нет доступных мониторов".into());
    }
    let mut overlays = lock(&OVERLAYS);
    let layout = topology(&monitors);
    if overlays.topology != layout || overlays.targets.iter().any(|t| app.get_webview_window(&t.label).is_none()) {
        for target in overlays.targets.drain(..) {
            if let Some(window) = app.get_webview_window(&target.label) {
                let _ = window.destroy();
            }
        }
        overlays.generation += 1;
        let capture: Vec<(u32, i32, i32)> =
            capture::monitors().map_err(|e| e.to_string())?.iter().map(|m| (m.id, m.x, m.y)).collect();
        for (index, monitor) in monitors.iter().enumerate() {
            let origin = (monitor.position().x, monitor.position().y);
            let Some(id) =
                match_monitor(desktop_origin(origin, monitor.scale_factor(), cfg!(target_os = "macos")), &capture)
            else {
                continue;
            };
            let label = format!("overlay-{}-{index}", overlays.generation);
            let window = create(app, &label, monitor).map_err(failed)?;
            raise(&window);
            let (p, s) = (monitor.position(), monitor.size());
            let bounds = (p.x as f64, p.y as f64, s.width as f64, s.height as f64);
            overlays.targets.push(Target { label, monitor: id, bounds });
        }
        overlays.topology = layout;
    }
    if overlays.targets.is_empty() {
        return Err("Не удалось сопоставить мониторы для снимка".into());
    }
    let _ = app.emit(events::OVERLAY_RESET, ());
    let cursor = app.cursor_position().ok();
    let mut focus = None;
    for target in &overlays.targets {
        let Some(window) = app.get_webview_window(&target.label) else { continue };
        window.show().map_err(failed)?;
        let (x, y, width, height) = target.bounds;
        let under_cursor = cursor.is_some_and(|c| c.x >= x && c.y >= y && c.x < x + width && c.y < y + height);
        if focus.is_none() || under_cursor {
            focus = Some(window);
        }
    }
    // Keyboard focus (Esc) goes to the overlay under the cursor.
    if let Some(window) = focus {
        let _ = window.set_focus();
    }
    Ok(())
}

fn create(app: &AppHandle, label: &str, monitor: &Monitor) -> tauri::Result<WebviewWindow> {
    let window = WebviewWindowBuilder::new(app, label, WebviewUrl::App("overlay.html".into()))
        .title("ctrlCctrlV")
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .focused(false)
        .visible(false)
        .build()?;
    let (position, size, scale) = (*monitor.position(), *monitor.size(), monitor.scale_factor());
    if cfg!(target_os = "macos") {
        // macOS places windows in points; converting with the monitor's own
        // scale stays right when monitors have different scales. Size goes
        // first: Cocoa keeps the bottom-left corner when resizing, so a window
        // placed at its default height and then enlarged grows up off screen.
        window.set_size(LogicalSize::new(size.width as f64 / scale, size.height as f64 / scale))?;
        window.set_position(LogicalPosition::new(position.x as f64 / scale, position.y as f64 / scale))?;
    } else {
        window.set_size(PhysicalSize::new(size.width, size.height))?;
        window.set_position(PhysicalPosition::new(position.x, position.y))?;
    }
    Ok(window)
}

/// Above the menu bar and over full-screen apps such as a VDI client.
#[cfg(target_os = "macos")]
fn raise(window: &WebviewWindow) {
    let target = window.clone();
    let _ = window.run_on_main_thread(move || {
        let Ok(ns_window) = target.ns_window() else { return };
        let ns_window = ns_window.cast::<objc2::runtime::AnyObject>();
        const SCREEN_SAVER_LEVEL: isize = 1000;
        const CAN_JOIN_ALL_SPACES: usize = 1 << 0;
        const FULL_SCREEN_AUXILIARY: usize = 1 << 8;
        unsafe {
            let _: () = objc2::msg_send![ns_window, setLevel: SCREEN_SAVER_LEVEL];
            let _: () = objc2::msg_send![ns_window, setCollectionBehavior: CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY];
        }
    });
}

#[cfg(not(target_os = "macos"))]
fn raise(_window: &WebviewWindow) {}

pub fn hide_all(app: &AppHandle) {
    for target in &lock(&OVERLAYS).targets {
        if let Some(window) = app.get_webview_window(&target.label) {
            let _ = window.hide();
        }
    }
}

/// The overlay reports a region in its own CSS pixels, which are the monitor's
/// logical pixels relative to its top-left corner. Commands are async so they
/// run off the main thread, which window calls made under `OVERLAYS` wait for.
#[tauri::command]
pub async fn selection_finished(app: AppHandle, window: WebviewWindow, x: f64, y: f64, width: f64, height: f64) {
    let monitor = lock(&OVERLAYS).targets.iter().find(|t| t.label == window.label()).map(|t| t.monitor);
    let shell = app.state::<Shell>();
    let operation = Operation::new();
    let append = matches!(*lock(&shell.activity), Activity::Selecting { append: true });
    if !shell.advance(Phase::Selecting, Activity::Recognizing(operation.clone())) {
        return;
    }
    hide_all(&app);
    let Some(monitor) = monitor else {
        shell.finish();
        return report(&app, Status::error(Phase::Idle, "Монитор отключён. Выделите область заново."));
    };
    let region = Region { x, y, width, height };
    tauri::async_runtime::spawn(ocr::recognize(app.clone(), operation, monitor, region, append));
}

#[tauri::command]
pub async fn selection_cancelled(app: AppHandle) {
    if app.state::<Shell>().advance(Phase::Selecting, Activity::Idle) {
        hide_all(&app);
        report(&app, Status::new(Phase::Idle, "Выделение отменено"));
    }
}
