//! Win32 keystrokes (`SendInput`) and the foreground window.
use std::mem::size_of;

use windows::Win32::Foundation::HWND;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, SendInput, VIRTUAL_KEY, VK_CONTROL, VK_HOME, VK_LWIN, VK_MENU, VK_RETURN,
    VK_RWIN, VK_SHIFT, VK_TAB,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::typing::Keyboard;
use crate::{Error, Permission, Permissions, Result};

/// These operations have no macOS-style privacy grant on Windows. `SendInput`
/// fails instead for protected or elevated target windows.
pub fn permissions() -> Permissions {
    Permissions { accessibility: true, screen_capture: true }
}

/// Returns whether a system prompt was shown; never on Windows.
pub fn request_permission(_: Permission) -> bool {
    false
}

pub fn open_permission_settings(_: Permission) -> Result<bool> {
    Ok(false)
}

/// The foreground window handle and its process id.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WindowTarget {
    hwnd: isize,
    pub pid: u32,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct WindowsKeyboard;

fn key_input(vk: VIRTUAL_KEY, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: vk, wScan: scan, dwFlags: flags, time: 0, dwExtraInfo: 0 } },
    }
}

fn send(events: &[INPUT]) -> Result<()> {
    let sent = unsafe { SendInput(events, size_of::<INPUT>() as i32) };
    if sent as usize != events.len() {
        // Release synthetic keys after a partial batch, including Shift.
        let releases: Vec<INPUT> = events
            .iter()
            .map(|e| unsafe { e.Anonymous.ki })
            .filter(|ki| !ki.dwFlags.contains(KEYEVENTF_KEYUP))
            .map(|ki| key_input(ki.wVk, ki.wScan, ki.dwFlags | KEYEVENTF_KEYUP))
            .collect();
        if !releases.is_empty() {
            unsafe { SendInput(&releases, size_of::<INPUT>() as i32) };
        }
        return Err(Error::Operation(
            "Windows отклонила ввод. Проверьте активное окно и уровни прав приложения и редактора.".into(),
        ));
    }
    Ok(())
}

fn press(vk: VIRTUAL_KEY, extended: bool) -> Result<()> {
    let flags = if extended { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    send(&[key_input(vk, 0, flags), key_input(vk, 0, flags | KEYEVENTF_KEYUP)])
}

impl Keyboard for WindowsKeyboard {
    type Target = WindowTarget;

    fn accessibility_granted(&self) -> bool {
        true
    }

    fn modifiers_pressed(&self) -> bool {
        [VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN]
            .iter()
            .any(|vk| unsafe { GetAsyncKeyState(vk.0 as i32) } as u16 & 0x8000 != 0)
    }

    fn focused_target(&self) -> Option<WindowTarget> {
        let hwnd = unsafe { GetForegroundWindow() };
        if hwnd.is_invalid() {
            return None;
        }
        let mut pid = 0u32;
        if unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) } == 0 {
            return None;
        }
        Some(WindowTarget { hwnd: hwnd.0 as isize, pid })
    }

    fn still_focused(&self, target: &WindowTarget) -> bool {
        let hwnd: HWND = unsafe { GetForegroundWindow() };
        hwnd.0 as isize == target.hwnd
    }

    fn character(&self, ch: char) -> Result<()> {
        let mut units = [0u16; 2];
        let events: Vec<INPUT> = ch
            .encode_utf16(&mut units)
            .iter()
            .flat_map(|&unit| {
                [
                    key_input(VIRTUAL_KEY(0), unit, KEYEVENTF_UNICODE),
                    key_input(VIRTUAL_KEY(0), unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                ]
            })
            .collect();
        send(&events)
    }

    fn enter(&self) -> Result<()> {
        press(VK_RETURN, false)
    }

    fn tab(&self) -> Result<()> {
        press(VK_TAB, false)
    }

    fn select_indentation(&self) -> Result<()> {
        send(&[
            key_input(VK_SHIFT, 0, KEYBD_EVENT_FLAGS(0)),
            key_input(VK_HOME, 0, KEYEVENTF_EXTENDEDKEY),
            key_input(VK_HOME, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
            key_input(VK_SHIFT, 0, KEYEVENTF_KEYUP),
        ])
    }
}
