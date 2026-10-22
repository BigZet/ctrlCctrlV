//! Shortcuts read from raw keyboard input (`WM_INPUT`).
//!
//! `RegisterHotKey` never fires while a remote-desktop or virtual-machine
//! window owns the keyboard: Citrix Workspace, Hyper-V and RDP clients
//! swallow the keys in a low-level hook. Raw input is delivered before the
//! hooks run, to any window registered with `RIDEV_INPUTSINK`, so a hidden
//! window on its own thread sees every key and matches the shortcuts itself.
//! The keys still reach the remote session.
use std::collections::HashSet;
use std::mem::size_of;
use std::sync::atomic::{AtomicIsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    VIRTUAL_KEY, VK_CONTROL, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_MENU, VK_RCONTROL, VK_RMENU, VK_RSHIFT,
    VK_RWIN, VK_SHIFT,
};
use windows::Win32::UI::Input::{
    GetRawInputData, HRAWINPUT, RAWINPUT, RAWINPUTDEVICE, RAWINPUTHEADER, RID_INPUT, RIDEV_INPUTSINK, RIM_TYPEKEYBOARD,
    RegisterRawInputDevices,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetMessageW, MSG, PostMessageW, PostQuitMessage,
    RegisterClassW, TranslateMessage, WINDOW_EX_STYLE, WM_CLOSE, WM_DESTROY, WM_INPUT, WNDCLASSW, WS_OVERLAPPED,
};
use windows::core::{PCWSTR, w};

use crate::hotkey::{Modifiers, Shortcut};
use crate::{Error, Result};

const RI_KEY_BREAK: u16 = 1;
const RI_KEY_E0: u16 = 2;
const HID_USAGE_PAGE_GENERIC: u16 = 1;
const HID_USAGE_GENERIC_KEYBOARD: u16 = 6;
const CLASS_NAME: PCWSTR = w!("ctrlCctrlV.RawInput");

type Handler = Box<dyn Fn(usize) + Send + Sync + 'static>;

struct Listener {
    /// Shortcuts by the index the handler is called with.
    shortcuts: Mutex<Vec<Shortcut>>,
    handler: Handler,
}

/// State of the listener thread: the keys held and the keys already reported
/// as pressed, so key repeat does not fire a shortcut again.
#[derive(Default)]
struct Keys {
    held: HashSet<u16>,
    fired: HashSet<u16>,
}

thread_local! {
    static LISTENER: std::cell::RefCell<Option<(Arc<Listener>, Keys)>> = const { std::cell::RefCell::new(None) };
}

/// Shortcuts matched from raw keyboard input on a background thread.
pub struct RawHotkeys {
    listener: Arc<Listener>,
    hwnd: Arc<AtomicIsize>,
    thread: Option<JoinHandle<()>>,
}

impl RawHotkeys {
    /// Starts listening; `handler` gets the index of the matched shortcut.
    pub fn start(shortcuts: Vec<Shortcut>, handler: impl Fn(usize) + Send + Sync + 'static) -> Result<Self> {
        let listener = Arc::new(Listener { shortcuts: Mutex::new(shortcuts), handler: Box::new(handler) });
        let hwnd = Arc::new(AtomicIsize::new(0));
        let (ready, started) = std::sync::mpsc::channel::<Result<()>>();
        let thread = {
            let (listener, hwnd) = (listener.clone(), hwnd.clone());
            std::thread::Builder::new()
                .name("rawinput".into())
                .spawn(move || run(listener, hwnd, ready))
                .map_err(|e| Error::Operation(format!("Не удалось запустить чтение клавиатуры: {e}")))?
        };
        match started.recv() {
            Ok(Ok(())) => Ok(Self { listener, hwnd, thread: Some(thread) }),
            Ok(Err(e)) => Err(e),
            Err(_) => Err(Error::Operation("Не удалось запустить чтение клавиатуры".into())),
        }
    }

    pub fn set_shortcuts(&self, shortcuts: Vec<Shortcut>) {
        *self.listener.shortcuts.lock().unwrap_or_else(|e| e.into_inner()) = shortcuts;
    }
}

impl Drop for RawHotkeys {
    fn drop(&mut self) {
        let hwnd = HWND(self.hwnd.load(Ordering::SeqCst) as _);
        if !hwnd.is_invalid() {
            let _ = unsafe { PostMessageW(Some(hwnd), WM_CLOSE, WPARAM(0), LPARAM(0)) };
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn run(listener: Arc<Listener>, hwnd_slot: Arc<AtomicIsize>, ready: std::sync::mpsc::Sender<Result<()>>) {
    LISTENER.with(|slot| *slot.borrow_mut() = Some((listener, Keys::default())));
    let hwnd = match create_window() {
        Ok(hwnd) => hwnd,
        Err(e) => {
            let _ = ready.send(Err(e));
            return;
        }
    };
    hwnd_slot.store(hwnd.0 as isize, Ordering::SeqCst);
    let _ = ready.send(Ok(()));
    let mut message = MSG::default();
    while unsafe { GetMessageW(&mut message, None, 0, 0) }.as_bool() {
        unsafe {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    hwnd_slot.store(0, Ordering::SeqCst);
    LISTENER.with(|slot| *slot.borrow_mut() = None);
}

fn create_window() -> Result<HWND> {
    let failed = |what: &str| Error::Operation(format!("Чтение клавиатуры недоступно: {what}"));
    let instance = unsafe { GetModuleHandleW(None) }.map_err(|e| failed(&e.to_string()))?;
    let class = WNDCLASSW {
        lpfnWndProc: Some(window_proc),
        hInstance: instance.into(),
        lpszClassName: CLASS_NAME,
        ..Default::default()
    };
    // Registering twice (after a restart) fails harmlessly; the class is still there.
    unsafe { RegisterClassW(&class) };
    // A real (never shown) window: message-only windows get no raw input.
    let hwnd = unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS_NAME,
            w!("ctrlCctrlV raw input"),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            None,
            None,
            Some(instance.into()),
            None,
        )
    }
    .map_err(|e| failed(&e.to_string()))?;
    let device = RAWINPUTDEVICE {
        usUsagePage: HID_USAGE_PAGE_GENERIC,
        usUsage: HID_USAGE_GENERIC_KEYBOARD,
        dwFlags: RIDEV_INPUTSINK,
        hwndTarget: hwnd,
    };
    if let Err(e) = unsafe { RegisterRawInputDevices(&[device], size_of::<RAWINPUTDEVICE>() as u32) } {
        let _ = unsafe { DestroyWindow(hwnd) };
        return Err(failed(&e.to_string()));
    }
    Ok(hwnd)
}

unsafe extern "system" fn window_proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match message {
        WM_INPUT => {
            if let Some(key) = read_key(HRAWINPUT(lparam.0 as _)) {
                LISTENER.with(|slot| {
                    if let Some((listener, keys)) = slot.borrow_mut().as_mut() {
                        on_key(listener, keys, key);
                    }
                });
            }
            unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
        }
        WM_CLOSE => {
            let _ = unsafe { DestroyWindow(hwnd) };
            LRESULT(0)
        }
        WM_DESTROY => {
            unsafe { PostQuitMessage(0) };
            LRESULT(0)
        }
        _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
    }
}

/// A key event: its virtual key (left/right modifiers told apart) and whether it went down.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct KeyEvent {
    vk: u16,
    down: bool,
}

fn read_key(handle: HRAWINPUT) -> Option<KeyEvent> {
    let mut input = RAWINPUT::default();
    let mut size = size_of::<RAWINPUT>() as u32;
    let header = size_of::<RAWINPUTHEADER>() as u32;
    let read = unsafe { GetRawInputData(handle, RID_INPUT, Some(&mut input as *mut _ as *mut _), &mut size, header) };
    if read == u32::MAX || input.header.dwType != RIM_TYPEKEYBOARD.0 {
        return None;
    }
    let keyboard = unsafe { input.data.keyboard };
    // 0xFF marks fake events that only carry scan-code prefixes.
    if keyboard.VKey == 0xFF {
        return None;
    }
    let extended = keyboard.Flags & RI_KEY_E0 != 0;
    let vk = match VIRTUAL_KEY(keyboard.VKey) {
        VK_CONTROL => {
            if extended {
                VK_RCONTROL
            } else {
                VK_LCONTROL
            }
        }
        VK_MENU => {
            if extended {
                VK_RMENU
            } else {
                VK_LMENU
            }
        }
        VK_SHIFT => {
            if keyboard.MakeCode == 0x36 {
                VK_RSHIFT
            } else {
                VK_LSHIFT
            }
        }
        other => other,
    };
    Some(KeyEvent { vk: vk.0, down: keyboard.Flags & RI_KEY_BREAK == 0 })
}

fn on_key(listener: &Listener, keys: &mut Keys, key: KeyEvent) {
    if !key.down {
        keys.held.remove(&key.vk);
        keys.fired.remove(&key.vk);
        return;
    }
    if !keys.held.insert(key.vk) || keys.fired.contains(&key.vk) {
        // Key repeat.
        return;
    }
    let modifiers = Modifiers {
        ctrl: keys.held.contains(&VK_LCONTROL.0) || keys.held.contains(&VK_RCONTROL.0),
        alt: keys.held.contains(&VK_LMENU.0) || keys.held.contains(&VK_RMENU.0),
        shift: keys.held.contains(&VK_LSHIFT.0) || keys.held.contains(&VK_RSHIFT.0),
        cmd: keys.held.contains(&VK_LWIN.0) || keys.held.contains(&VK_RWIN.0),
    };
    let matched = {
        let shortcuts = listener.shortcuts.lock().unwrap_or_else(|e| e.into_inner());
        shortcuts.iter().position(|shortcut| {
            shortcut.modifiers == modifiers && super::windows::shortcut_key(shortcut.key).0 == key.vk
        })
    };
    if let Some(index) = matched {
        keys.fired.insert(key.vk);
        (listener.handler)(index);
    }
}
