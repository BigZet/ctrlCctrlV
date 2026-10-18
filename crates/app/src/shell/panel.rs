//! The typing panel: a small topmost window with progress and pause, continue
//! and stop buttons. It never takes focus from the window being typed into:
//! `WS_EX_NOACTIVATE` on Windows, a non-activating `NSPanel` on macOS.
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, LogicalPosition, LogicalSize, Manager, PhysicalPosition, WebviewUrl, WebviewWindow};
use tauri::{Monitor, WebviewWindowBuilder};

use super::{Shell, lock};

const LABEL: &str = "typing";
/// Logical size of the panel.
const SIZE: (f64, f64) = (380.0, 126.0);
/// Distance from the right and bottom edges of the monitor, clear of the taskbar and Dock.
const MARGIN: (f64, f64) = (24.0, 96.0);
const HIDE_AFTER: Duration = Duration::from_millis(2500);

/// Where the user dragged the panel, kept while the app runs.
static PLACE: Mutex<Option<PhysicalPosition<i32>>> = Mutex::new(None);

pub fn show(app: &AppHandle) {
    let window = match app.get_webview_window(LABEL) {
        Some(window) => window,
        None => match create(app) {
            Ok(window) => window,
            // Typing works without the panel.
            Err(_) => return,
        },
    };
    if window.is_visible().unwrap_or(false) {
        return;
    }
    place(app, &window);
    let _ = window.show();
}

fn create(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let window = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("typing.html".into()))
        .title("ctrlCctrlV · печать")
        .inner_size(SIZE.0, SIZE.1)
        .decorations(false)
        .transparent(true)
        .shadow(cfg!(target_os = "macos"))
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .focusable(false)
        .accept_first_mouse(true)
        .visible(false)
        .build()?;
    non_activating(&window);
    Ok(window)
}

/// The last place the user left the panel while it is on a monitor, else
/// the bottom-right corner of the monitor under the cursor.
fn place(app: &AppHandle, window: &WebviewWindow) {
    if let Some(position) = *lock(&PLACE)
        && app.monitor_from_point(position.x as f64, position.y as f64).ok().flatten().is_some()
    {
        let _ = window.set_position(position);
        return;
    }
    let cursor = app.cursor_position().ok();
    let monitor = cursor
        .and_then(|c| app.monitor_from_point(c.x, c.y).ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten());
    if let Some(monitor) = monitor {
        let _ = window.set_size(LogicalSize::new(SIZE.0, SIZE.1));
        let _ = set_corner(window, &monitor);
    }
}

fn set_corner(window: &WebviewWindow, monitor: &Monitor) -> tauri::Result<()> {
    let (position, size, scale) = (*monitor.position(), *monitor.size(), monitor.scale_factor());
    let x = (size.width as f64 / scale - SIZE.0 - MARGIN.0).max(0.0);
    let y = (size.height as f64 / scale - SIZE.1 - MARGIN.1).max(0.0);
    if cfg!(target_os = "macos") {
        // macOS places windows in points; see the overlay for why.
        window.set_position(LogicalPosition::new(position.x as f64 / scale + x, position.y as f64 / scale + y))
    } else {
        window.set_position(PhysicalPosition::new(position.x + (x * scale) as i32, position.y + (y * scale) as i32))
    }
}

/// Hides the panel a moment after job `id` ended, unless another job started.
pub fn hide_later(app: &AppHandle, id: u64) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(HIDE_AFTER).await;
        let ended = lock(&app.state::<Shell>().typing).as_ref().is_none_or(|job| job.id == id && job.state.finished());
        if ended {
            hide(&app);
        }
    });
}

fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) {
        if let Ok(position) = window.outer_position() {
            *lock(&PLACE) = Some(position);
        }
        let _ = window.hide();
    }
}

/// Turns the window into a non-activating panel: clicking it neither activates
/// the app nor makes it key, so the keystrokes keep going to the VDI client.
/// This is the class swap `tauri-nspanel` does: tao's `TaoWindow` adds no
/// state the panel class needs and nothing here calls tao's focus setters.
#[cfg(target_os = "macos")]
fn non_activating(window: &WebviewWindow) {
    use std::sync::OnceLock;

    use objc2::runtime::{AnyClass, AnyObject, Bool, ClassBuilder, Sel};
    use objc2::{msg_send, sel};

    struct Class(&'static AnyClass);
    // A registered class is immutable and shared by every thread.
    unsafe impl Send for Class {}
    unsafe impl Sync for Class {}
    static PANEL: OnceLock<Option<Class>> = OnceLock::new();

    extern "C" fn no(_: &AnyObject, _: Sel) -> Bool {
        Bool::NO
    }

    let target = window.clone();
    let _ = window.run_on_main_thread(move || {
        let class = PANEL.get_or_init(|| {
            let mut builder = ClassBuilder::new(c"CtrlCctrlVTypingPanel", AnyClass::get(c"NSPanel")?)?;
            unsafe {
                builder.add_method(sel!(canBecomeKeyWindow), no as extern "C" fn(_, _) -> _);
                builder.add_method(sel!(canBecomeMainWindow), no as extern "C" fn(_, _) -> _);
            }
            Some(Class(builder.register()))
        });
        let (Some(Class(class)), Ok(ns_window)) = (class, target.ns_window()) else { return };
        const NONACTIVATING_PANEL: usize = 1 << 7;
        const STATUS_LEVEL: isize = 25;
        const CAN_JOIN_ALL_SPACES: usize = 1 << 0;
        const FULL_SCREEN_AUXILIARY: usize = 1 << 8;
        unsafe {
            let ns_window = &*ns_window.cast::<AnyObject>();
            AnyObject::set_class(ns_window, class);
            let mask: usize = msg_send![ns_window, styleMask];
            let _: () = msg_send![ns_window, setStyleMask: mask | NONACTIVATING_PANEL];
            let _: () = msg_send![ns_window, setFloatingPanel: Bool::YES];
            let _: () = msg_send![ns_window, setBecomesKeyOnlyIfNeeded: Bool::YES];
            // Panels hide when their app is inactive, which is always the case here.
            let _: () = msg_send![ns_window, setHidesOnDeactivate: Bool::NO];
            let _: () = msg_send![ns_window, setLevel: STATUS_LEVEL];
            let _: () = msg_send![ns_window, setCollectionBehavior: CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY];
        }
    });
}

#[cfg(not(target_os = "macos"))]
fn non_activating(_window: &WebviewWindow) {}
