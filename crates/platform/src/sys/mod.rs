#[cfg(target_os = "macos")]
mod macos;
#[cfg(windows)]
mod windows;

#[cfg(target_os = "macos")]
pub use macos::{
    open_permission_settings, permissions, request_permission, MacKeyboard as NativeKeyboard,
};
#[cfg(windows)]
pub use windows::{
    open_permission_settings, permissions, request_permission, WindowsKeyboard as NativeKeyboard,
};
