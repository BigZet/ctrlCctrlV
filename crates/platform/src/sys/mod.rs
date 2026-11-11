#[cfg(target_os = "macos")]
mod macos;
#[cfg(windows)]
pub(crate) mod rawinput;
#[cfg(windows)]
mod windows;

#[cfg(target_os = "macos")]
pub use macos::{MacKeyboard as NativeKeyboard, open_permission_settings, permissions, request_permission, show_error};
#[cfg(windows)]
pub use windows::{
    WindowsKeyboard as NativeKeyboard, open_permission_settings, permissions, request_permission, show_error,
};
