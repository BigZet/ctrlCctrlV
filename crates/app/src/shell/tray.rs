//! Tray icon and its menu.
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager};

use super::{Action, Shell};
use crate::model::Status;

const TRAY: &str = "tray";

fn menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let settings = app.state::<Shell>().settings();
    let item = |id: &str, label: String| MenuItem::with_id(app, id, label, true, None::<&str>);
    let capture = item("capture", format!("Распознать область\t{}", settings.capture_hotkey))?;
    let append = item("captureAppend", "Добавить область к результату".into())?;
    // Opening the menu can move focus (to the taskbar on Windows), so typing
    // from it waits for the user to return to the target window.
    let delayed = item("typeDelayed", "Напечатать буфер через 3 с".into())?;
    let cancel = item("cancel", format!("Остановить\t{}", settings.cancel_hotkey))?;
    let window = item("showWindow", "Открыть окно".into())?;
    let quit = item("quit", "Выход".into())?;
    let separator = PredefinedMenuItem::separator(app)?;
    Menu::with_items(app, &[&capture, &append, &delayed, &cancel, &separator, &window, &quit])
}

/// Shows changed hotkeys in the menu.
pub fn refresh(app: &AppHandle) -> tauri::Result<()> {
    if let Some(tray) = app.tray_by_id(TRAY) {
        tray.set_menu(Some(menu(app)?))?;
    }
    Ok(())
}

pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let menu = menu(app)?;
    let mut tray =
        TrayIconBuilder::with_id(TRAY).tooltip("ctrlCctrlV").menu(&menu).show_menu_on_left_click(true).on_menu_event(
            |app, event| {
                let action = match event.id().as_ref() {
                    "capture" => Action::Capture,
                    "captureAppend" => Action::CaptureAppend,
                    "typeDelayed" => Action::TypeDelayed,
                    "cancel" => Action::Cancel,
                    "showWindow" => Action::ShowWindow,
                    "quit" => Action::Quit,
                    _ => return,
                };
                tauri::async_runtime::spawn(super::dispatch(app.clone(), action));
            },
        );
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

pub fn show_status(app: &AppHandle, status: &Status) {
    if let Some(tray) = app.tray_by_id(TRAY) {
        let _ = tray.set_tooltip(Some(format!("ctrlCctrlV · {}", status.message)));
    }
}
