//! Native integration for Windows x64 and macOS ARM64: global shortcuts,
//! keystroke emulation, the focused window, privacy permissions, region
//! capture and the clipboard.
//!
//! The typing policy, shortcut parsing and PNG encoding are portable so their
//! tests run anywhere; everything that calls the OS is behind `cfg`.

pub mod capture;
pub mod hotkey;
pub mod typing;

#[cfg(any(windows, target_os = "macos"))]
pub mod clipboard;
#[cfg(any(windows, target_os = "macos"))]
mod sys;

#[cfg(any(windows, target_os = "macos"))]
pub use sys::{open_permission_settings, permissions, request_permission, NativeKeyboard};

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum Error {
    /// Stopped on purpose: the user cancelled, focus moved or a modifier was pressed.
    #[error("{0}")]
    Cancelled(String),
    #[error("{0}")]
    Operation(String),
}

pub type Result<T> = std::result::Result<T, Error>;

/// Privacy grants the app needs. Global shortcuts use Carbon hot keys on
/// macOS, so Input Monitoring is no longer required.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Permission {
    /// Posting keystrokes and reading the focused window.
    Accessibility,
    ScreenCapture,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Permissions {
    pub accessibility: bool,
    pub screen_capture: bool,
}

impl Permissions {
    pub fn granted(&self, permission: Permission) -> bool {
        match permission {
            Permission::Accessibility => self.accessibility,
            Permission::ScreenCapture => self.screen_capture,
        }
    }
}
