//! Win32 keystrokes (`SendInput`) and the foreground window.
use std::mem::size_of;

use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, GetKeyState, GetKeyboardLayout, GetKeyboardLayoutList, HKL, INPUT, INPUT_0, INPUT_KEYBOARD,
    KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_UNICODE,
    MAPVK_VK_TO_VSC, MAPVK_VK_TO_VSC_EX, MapVirtualKeyExW, MapVirtualKeyW, SendInput, VIRTUAL_KEY, VK_CAPITAL,
    VK_CONTROL, VK_DELETE, VK_ESCAPE, VK_HOME, VK_LWIN, VK_MENU, VK_RETURN, VK_RWIN, VK_SHIFT, VK_TAB, VkKeyScanExW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetWindowThreadProcessId, PostMessageW, WM_INPUTLANGCHANGEREQUEST,
};

use crate::hotkey::{Key, Shortcut};
use crate::typing::{Keyboard, Script};
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

/// A virtual key with its scan code, as a real keyboard sends it: remote
/// desktop and VDI clients forward the scan code and drop events without one.
fn key(vk: VIRTUAL_KEY, extended: bool, up: bool) -> INPUT {
    let scan = unsafe { MapVirtualKeyW(vk.0 as u32, MAPVK_VK_TO_VSC) } as u16;
    let mut flags = if extended { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    if up {
        flags |= KEYEVENTF_KEYUP;
    }
    key_input(vk, scan, flags)
}

fn press(vk: VIRTUAL_KEY, extended: bool) -> Result<()> {
    send(&[key(vk, extended, false), key(vk, extended, true)])
}

/// A key typed by its scan code (`MAPVK_VK_TO_VSC_EX`, `0xE0..` when extended).
fn scan_input(vk: VIRTUAL_KEY, scan: u32, up: bool) -> INPUT {
    let mut flags = KEYEVENTF_SCANCODE;
    if scan & 0xFF00 == 0xE000 {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    if up {
        flags |= KEYEVENTF_KEYUP;
    }
    key_input(vk, scan as u16 & 0xFF, flags)
}

/// The virtual key a shortcut ends with.
pub(crate) fn shortcut_key(key: Key) -> VIRTUAL_KEY {
    match key {
        Key::Escape => VK_ESCAPE,
        Key::Char(c) => VIRTUAL_KEY(c.to_ascii_uppercase() as u16),
    }
}

/// The modifier keys of a shortcut, in press order.
pub(crate) fn shortcut_modifiers(shortcut: &Shortcut) -> Vec<VIRTUAL_KEY> {
    let m = shortcut.modifiers;
    [(m.ctrl, VK_CONTROL), (m.alt, VK_MENU), (m.shift, VK_SHIFT), (m.cmd, VK_LWIN)]
        .into_iter()
        .filter(|(on, _)| *on)
        .map(|(_, vk)| vk)
        .collect()
}

fn foreground_layout() -> Option<HKL> {
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.is_invalid() {
        return None;
    }
    let thread = unsafe { GetWindowThreadProcessId(hwnd, None) };
    (thread != 0).then(|| unsafe { GetKeyboardLayout(thread) })
}

/// The virtual key and Shift/Ctrl/Alt state (bits 1/2/4) typing `ch` in `layout`.
fn layout_key(ch: char, layout: HKL) -> Option<(VIRTUAL_KEY, u8)> {
    let unit = u16::try_from(ch as u32).ok()?;
    let found = unsafe { VkKeyScanExW(unit, layout) };
    let [vk, state] = found.to_le_bytes();
    // Ctrl without Alt makes control characters, not text; other bits are
    // layout-specific (Kana and similar) modifiers.
    if found == -1 || vk == 0xFF || state & !0b111 != 0 || state & 0b110 == 0b010 {
        return None;
    }
    Some((VIRTUAL_KEY(vk as u16), state))
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

    fn owned_by_this_app(&self, target: &WindowTarget) -> bool {
        target.pid == std::process::id()
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

    fn layout(&self) -> Option<isize> {
        foreground_layout().map(|hkl| hkl.0 as isize)
    }

    fn layout_with(&self, ch: char, current: isize) -> Option<isize> {
        let current = HKL(current as _);
        if layout_key(ch, current).is_some() {
            return Some(current.0 as isize);
        }
        let count = unsafe { GetKeyboardLayoutList(None) }.max(0) as usize;
        let mut layouts = vec![HKL::default(); count];
        let count = unsafe { GetKeyboardLayoutList(Some(&mut layouts)) }.max(0) as usize;
        layouts.truncate(count);
        layouts.into_iter().find(|&hkl| layout_key(ch, hkl).is_some()).map(|hkl| hkl.0 as isize)
    }

    fn switch_layout(&self, layout: isize) -> Result<()> {
        let hwnd = unsafe { GetForegroundWindow() };
        unsafe { PostMessageW(Some(hwnd), WM_INPUTLANGCHANGEREQUEST, WPARAM(0), LPARAM(layout)) }
            .map_err(|e| Error::Operation(format!("Не удалось переключить раскладку: {e}")))
    }

    fn layout_script(&self, layout: isize) -> Option<Script> {
        // The low word of an HKL is its language id; its low byte the primary language.
        match (layout as u16) & 0xFF {
            0x09 => Some(Script::Latin),
            0x19 => Some(Script::Cyrillic),
            _ => None,
        }
    }

    fn shortcut(&self, shortcut: &Shortcut) -> Result<()> {
        let modifiers = shortcut_modifiers(shortcut);
        let vk = shortcut_key(shortcut.key);
        let mut events: Vec<INPUT> = modifiers.iter().map(|&m| key(m, false, false)).collect();
        events.push(key(vk, false, false));
        events.push(key(vk, false, true));
        events.extend(modifiers.iter().rev().map(|&m| key(m, false, true)));
        send(&events)
    }

    fn delete(&self) -> Result<()> {
        press(VK_DELETE, true)
    }

    fn layout_character(&self, ch: char, layout: isize) -> Result<()> {
        let hkl = HKL(layout as _);
        let Some((vk, mut state)) = layout_key(ch, hkl) else {
            return self.character(ch);
        };
        // Caps Lock inverts Shift for letters.
        if ch.is_alphabetic() && unsafe { GetKeyState(VK_CAPITAL.0 as i32) } & 1 != 0 {
            state ^= 1;
        }
        let modifiers: Vec<VIRTUAL_KEY> = [(1, VK_SHIFT), (2, VK_CONTROL), (4, VK_MENU)]
            .into_iter()
            .filter(|(bit, _)| state & bit != 0)
            .map(|(_, vk)| vk)
            .collect();
        let scan = unsafe { MapVirtualKeyExW(vk.0 as u32, MAPVK_VK_TO_VSC_EX, Some(hkl)) };
        if scan == 0 {
            return self.character(ch);
        }
        let mut events: Vec<INPUT> = modifiers.iter().map(|&m| key(m, false, false)).collect();
        events.push(scan_input(vk, scan, false));
        events.push(scan_input(vk, scan, true));
        events.extend(modifiers.iter().rev().map(|&m| key(m, false, true)));
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
            key(VK_SHIFT, false, false),
            key(VK_HOME, true, false),
            key(VK_HOME, true, true),
            key(VK_SHIFT, false, true),
        ])
    }
}

/// A blocking error dialog, for failures before the tray icon exists.
pub fn show_error(title: &str, message: &str) {
    use windows::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
    use windows::core::PCWSTR;
    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let (title, message) = (wide(title), wide(message));
    unsafe {
        let _ = MessageBoxW(None, PCWSTR(message.as_ptr()), PCWSTR(title.as_ptr()), MB_OK | MB_ICONERROR);
    }
}
