//! ctrlCctrlV desktop shell: tray icon, global hotkeys, region selection, OCR
//! into the clipboard and typing the clipboard into the focused window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
// Elsewhere only the portable logic and its tests are built.
#![cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]

mod model;
#[cfg(any(windows, target_os = "macos"))]
mod shell;

#[cfg(any(windows, target_os = "macos"))]
fn main() {
    shell::run();
}

#[cfg(not(any(windows, target_os = "macos")))]
fn main() {
    eprintln!("ctrlCctrlV runs on Windows and macOS only");
    std::process::exit(1);
}
