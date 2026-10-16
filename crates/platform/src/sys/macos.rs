//! Quartz keystrokes (`CGEventPost`), the focused AX window and privacy grants.
use std::ptr::{self, NonNull};

use objc2_app_kit::NSWorkspace;
use objc2_application_services::{
    kAXTrustedCheckOptionPrompt, AXError, AXIsProcessTrusted, AXIsProcessTrustedWithOptions,
    AXUIElement,
};
use objc2_core_foundation::{CFBoolean, CFDictionary, CFRetained, CFString, CFType};
use objc2_core_graphics::{
    CGEvent, CGEventFlags, CGEventSource, CGEventSourceStateID, CGEventTapLocation, CGKeyCode,
    CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess,
};

use crate::typing::Keyboard;
use crate::{Error, Permission, Permissions, Result};

const RETURN: CGKeyCode = 36;
const TAB: CGKeyCode = 48;
const HOME: CGKeyCode = 115;

pub fn permissions() -> Permissions {
    Permissions {
        accessibility: unsafe { AXIsProcessTrusted() },
        screen_capture: CGPreflightScreenCaptureAccess(),
    }
}

/// Ask macOS to prompt for `permission`. Returns whether a prompt was requested
/// (false when it is already granted).
pub fn request_permission(permission: Permission) -> bool {
    if permissions().granted(permission) {
        return false;
    }
    match permission {
        Permission::Accessibility => {
            let key: &CFString = unsafe { kAXTrustedCheckOptionPrompt };
            let options = CFDictionary::from_slices(&[key], &[CFBoolean::new(true)]);
            unsafe { AXIsProcessTrustedWithOptions(Some(options.as_opaque())) };
        }
        Permission::ScreenCapture => {
            CGRequestScreenCaptureAccess();
        }
    }
    true
}

/// Open the matching Privacy & Security pane. Returns false when already granted.
pub fn open_permission_settings(permission: Permission) -> Result<bool> {
    // A deep link alone does not register this app with macOS privacy controls.
    if !request_permission(permission) {
        return Ok(false);
    }
    let pane = match permission {
        Permission::Accessibility => "Privacy_Accessibility",
        Permission::ScreenCapture => "Privacy_ScreenCapture",
    };
    let opened = std::process::Command::new("/usr/bin/open")
        .arg(format!(
            "x-apple.systempreferences:com.apple.preference.security?{pane}"
        ))
        .status()
        .is_ok_and(|status| status.success());
    if !opened {
        return Err(Error::Operation(
            "Не удалось открыть системные настройки macOS".into(),
        ));
    }
    Ok(true)
}

fn frontmost_pid() -> Option<i32> {
    NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .map(|app| app.processIdentifier())
}

/// The frontmost application and its focused AX window.
#[derive(Debug, Clone)]
pub struct WindowTarget {
    pub pid: i32,
    window: CFRetained<AXUIElement>,
}

// AX elements are immutable CF references; retain/release and CFEqual are thread-safe.
unsafe impl Send for WindowTarget {}
unsafe impl Sync for WindowTarget {}

impl PartialEq for WindowTarget {
    fn eq(&self, other: &Self) -> bool {
        // CFEqual compares the native window identity, not the wrapper.
        self.pid == other.pid && *self.window == *other.window
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct MacKeyboard;

fn post(code: CGKeyCode, flags: CGEventFlags, text: Option<&[u16]>) -> Result<()> {
    for down in [true, false] {
        let event = CGEvent::new_keyboard_event(None, code, down)
            .ok_or_else(|| Error::Operation("macOS не создала событие клавиатуры".into()))?;
        CGEvent::set_flags(Some(&event), flags);
        if let Some(units) = text {
            // Quartz expects UTF-16 code units; non-BMP characters are a surrogate pair.
            unsafe {
                CGEvent::keyboard_set_unicode_string(Some(&event), units.len() as _, units.as_ptr())
            };
        }
        CGEvent::post(CGEventTapLocation::HIDEventTap, Some(&event));
    }
    Ok(())
}

impl Keyboard for MacKeyboard {
    type Target = WindowTarget;

    fn accessibility_granted(&self) -> bool {
        unsafe { AXIsProcessTrusted() }
    }

    fn modifiers_pressed(&self) -> bool {
        let mask = CGEventFlags::MaskCommand
            | CGEventFlags::MaskControl
            | CGEventFlags::MaskAlternate
            | CGEventFlags::MaskShift;
        CGEventSource::flags_state(CGEventSourceStateID::CombinedSessionState).intersects(mask)
    }

    /// An unavailable AX identity must stop input, so every failure is `None`.
    fn focused_target(&self) -> Option<WindowTarget> {
        let pid = frontmost_pid()?;
        let application = unsafe { AXUIElement::new_application(pid) };
        if unsafe { application.set_messaging_timeout(0.2) } != AXError::Success {
            return None;
        }
        let mut value: *const CFType = ptr::null();
        let attribute = CFString::from_static_str("AXFocusedWindow");
        let error =
            unsafe { application.copy_attribute_value(&attribute, NonNull::from(&mut value)) };
        let value = NonNull::new(value.cast_mut())?;
        // Copy* returns a +1 reference; take ownership before any early return.
        let value = unsafe { CFRetained::from_raw(value) };
        if error != AXError::Success {
            return None;
        }
        let window = value.downcast::<AXUIElement>().ok()?;
        let mut owner = 0;
        if unsafe { window.pid(NonNull::from(&mut owner)) } != AXError::Success
            || owner != pid
            || frontmost_pid() != Some(pid)
        {
            return None;
        }
        Some(WindowTarget { pid, window })
    }

    fn still_focused(&self, target: &WindowTarget) -> bool {
        frontmost_pid() == Some(target.pid)
    }

    fn character(&self, ch: char) -> Result<()> {
        let mut units = [0u16; 2];
        post(0, CGEventFlags(0), Some(ch.encode_utf16(&mut units)))
    }

    fn enter(&self) -> Result<()> {
        post(RETURN, CGEventFlags(0), None)
    }

    fn tab(&self) -> Result<()> {
        post(TAB, CGEventFlags(0), None)
    }

    fn select_indentation(&self) -> Result<()> {
        post(HOME, CGEventFlags::MaskShift, None)
    }
}
