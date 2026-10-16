//! Cancellable typing policy shared by the native keyboards.
//!
//! The full window identity (an AX query on macOS) is read once at the start
//! of a run and again after each newline. Before every keystroke only the cheap
//! focus check runs (`GetForegroundWindow` / the frontmost pid).
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use crate::{Error, Result};

pub const FOCUS_CHANGED: &str = "Печать остановлена: активное приложение или окно изменилось";
const STOPPED: &str = "Печать остановлена";
const MODIFIER_PRESSED: &str = "Печать остановлена: нажата клавиша-модификатор";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TypingSettings {
    pub character_delay: Duration,
    pub newline_delay: Duration,
    /// Select the editor's automatic indentation (Shift+Home) after each newline
    /// so the next typed character replaces it.
    pub autoindent: bool,
    /// Type this many spaces for a tab; 0 sends the Tab key.
    pub spaces_per_tab: u8,
}

impl Default for TypingSettings {
    fn default() -> Self {
        Self {
            character_delay: Duration::from_millis(8),
            newline_delay: Duration::from_millis(60),
            autoindent: false,
            spaces_per_tab: 0,
        }
    }
}

/// Native keystroke emulation. Implementations post events to the focused window.
pub trait Keyboard {
    /// Identity of the focused window, compared to detect a focus change.
    type Target: PartialEq;

    fn accessibility_granted(&self) -> bool;
    fn modifiers_pressed(&self) -> bool;
    /// Full identity of the focused window. May be slow (an AX query on macOS).
    fn focused_target(&self) -> Option<Self::Target>;
    /// Cheap check that `target`'s application or window still owns focus.
    fn still_focused(&self, target: &Self::Target) -> bool;
    fn character(&self, ch: char) -> Result<()>;
    fn enter(&self) -> Result<()>;
    fn tab(&self) -> Result<()>;
    fn select_indentation(&self) -> Result<()>;
}

/// Cancellation flag whose waits wake up as soon as it is set.
#[derive(Clone, Default)]
pub struct CancelToken(Arc<(Mutex<bool>, Condvar)>);

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        let (flag, wake) = &*self.0;
        *flag.lock().unwrap_or_else(|e| e.into_inner()) = true;
        wake.notify_all();
    }

    pub fn is_cancelled(&self) -> bool {
        *self.0.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Sleep for `timeout` unless cancelled first. Returns whether it was cancelled.
    pub fn wait(&self, timeout: Duration) -> bool {
        let (flag, wake) = &*self.0;
        let guard = flag.lock().unwrap_or_else(|e| e.into_inner());
        if timeout.is_zero() {
            return *guard;
        }
        let (guard, _) = wake
            .wait_timeout_while(guard, timeout, |cancelled| !*cancelled)
            .unwrap_or_else(|e| e.into_inner());
        *guard
    }
}

/// Type `text` into the focused window.
///
/// `expected` is the window that was focused when typing was requested; if
/// focus moved before the run starts nothing is typed.
pub fn type_text<K: Keyboard>(
    keyboard: &K,
    text: &str,
    settings: &TypingSettings,
    cancel: &CancelToken,
    mut progress: impl FnMut(usize, usize),
    expected: Option<&K::Target>,
) -> Result<()> {
    if !keyboard.accessibility_granted() {
        return Err(Error::Operation(
            "Разрешите управление вводом в разделе «Система»".into(),
        ));
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while keyboard.modifiers_pressed() {
        if cancel.wait(Duration::from_millis(20)) {
            return Err(Error::Cancelled(STOPPED.into()));
        }
        if Instant::now() >= deadline {
            return Err(Error::Operation("Отпустите клавиши сочетания перед печатью".into()));
        }
    }
    let target = keyboard.focused_target();
    if expected.is_some_and(|expected| target.as_ref() != Some(expected)) {
        return Err(Error::Cancelled(FOCUS_CHANGED.into()));
    }
    let target = target.ok_or_else(|| Error::Operation("Нет активного окна для печати".into()))?;

    let check = || -> Result<()> {
        if cancel.is_cancelled() {
            return Err(Error::Cancelled(STOPPED.into()));
        }
        if !keyboard.still_focused(&target) {
            return Err(Error::Cancelled(FOCUS_CHANGED.into()));
        }
        if keyboard.modifiers_pressed() {
            return Err(Error::Cancelled(MODIFIER_PRESSED.into()));
        }
        Ok(())
    };

    let chars: Vec<char> = text.replace("\r\n", "\n").replace('\r', "\n").chars().collect();
    for (index, &ch) in chars.iter().enumerate() {
        check()?;
        match ch {
            '\n' => {
                keyboard.enter()?;
                cancel.wait(settings.newline_delay);
                check()?;
                // A mouse click can switch windows inside the same application
                // without changing the cheap check, so re-read the full identity per line.
                if keyboard.focused_target().as_ref() != Some(&target) {
                    return Err(Error::Cancelled(FOCUS_CHANGED.into()));
                }
                if settings.autoindent && index + 1 < chars.len() {
                    keyboard.select_indentation()?;
                }
            }
            '\t' if settings.spaces_per_tab > 0 => {
                for _ in 0..settings.spaces_per_tab {
                    check()?;
                    keyboard.character(' ')?;
                    cancel.wait(settings.character_delay);
                }
            }
            '\t' => keyboard.tab()?,
            _ => keyboard.character(ch)?,
        }
        progress(index + 1, chars.len());
        cancel.wait(settings.character_delay);
    }
    check()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[derive(Debug, Clone, PartialEq)]
    enum Event {
        Text(char),
        Enter,
        Tab,
        Indent,
    }

    struct Fake {
        events: RefCell<Vec<Event>>,
        pid: Cell<u32>,
        full_checks: Cell<usize>,
        on_character: Option<Box<dyn Fn()>>,
    }

    impl Fake {
        fn new() -> Self {
            Self {
                events: RefCell::default(),
                pid: Cell::new(100),
                full_checks: Cell::new(0),
                on_character: None,
            }
        }
        fn events(&self) -> Vec<Event> {
            self.events.borrow().clone()
        }
    }

    impl Keyboard for Fake {
        type Target = u32;
        fn accessibility_granted(&self) -> bool {
            true
        }
        fn modifiers_pressed(&self) -> bool {
            false
        }
        fn focused_target(&self) -> Option<u32> {
            self.full_checks.set(self.full_checks.get() + 1);
            Some(self.pid.get())
        }
        fn still_focused(&self, target: &u32) -> bool {
            self.pid.get() == *target
        }
        fn character(&self, ch: char) -> Result<()> {
            self.events.borrow_mut().push(Event::Text(ch));
            if let Some(hook) = &self.on_character {
                hook();
            }
            Ok(())
        }
        fn enter(&self) -> Result<()> {
            self.events.borrow_mut().push(Event::Enter);
            Ok(())
        }
        fn tab(&self) -> Result<()> {
            self.events.borrow_mut().push(Event::Tab);
            Ok(())
        }
        fn select_indentation(&self) -> Result<()> {
            self.events.borrow_mut().push(Event::Indent);
            Ok(())
        }
    }

    fn fast() -> TypingSettings {
        TypingSettings {
            character_delay: Duration::from_millis(1),
            newline_delay: Duration::ZERO,
            ..Default::default()
        }
    }

    #[test]
    fn unicode_crlf_tabs_and_autoindent() {
        let fake = Fake::new();
        let settings = TypingSettings {
            autoindent: true,
            spaces_per_tab: 2,
            ..fast()
        };
        type_text(&fake, "Ж\r\n\t🙂\n", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        use Event::*;
        assert_eq!(
            fake.events(),
            [Text('Ж'), Enter, Indent, Text(' '), Text(' '), Text('🙂'), Enter]
        );
    }

    #[test]
    fn tab_key_without_expansion() {
        let fake = Fake::new();
        type_text(&fake, "\tx", &fast(), &CancelToken::new(), |_, _| {}, None).unwrap();
        assert_eq!(fake.events(), [Event::Tab, Event::Text('x')]);
    }

    #[test]
    fn full_window_identity_is_read_once_per_run_and_per_line() {
        let fake = Fake::new();
        type_text(&fake, "abcdef\nghij", &fast(), &CancelToken::new(), |_, _| {}, None).unwrap();
        assert_eq!(fake.full_checks.get(), 2);
    }

    #[test]
    fn focus_change_stops_before_next_character() {
        let fake = Fake::new();
        let err = type_text(
            &fake,
            "abc",
            &fast(),
            &CancelToken::new(),
            |_, _| fake.pid.set(200),
            None,
        )
        .unwrap_err();
        assert!(matches!(err, Error::Cancelled(ref m) if m.contains("приложение")));
        assert_eq!(fake.events(), [Event::Text('a')]);
    }

    #[test]
    fn cancel_stops_before_next_character() {
        let fake = Fake::new();
        let cancel = CancelToken::new();
        let err = type_text(&fake, "abc", &fast(), &cancel, |_, _| cancel.cancel(), None).unwrap_err();
        assert!(matches!(err, Error::Cancelled(_)));
        assert_eq!(fake.events(), [Event::Text('a')]);
    }

    #[test]
    fn cancel_mid_tab_expansion_stops_remaining_spaces() {
        let cancel = CancelToken::new();
        let mut fake = Fake::new();
        let hook = cancel.clone();
        fake.on_character = Some(Box::new(move || hook.cancel()));
        let settings = TypingSettings {
            spaces_per_tab: 4,
            ..fast()
        };
        assert!(type_text(&fake, "\tabc", &settings, &cancel, |_, _| {}, None).is_err());
        assert_eq!(fake.events(), [Event::Text(' ')]);
    }

    #[test]
    fn focus_change_before_start_never_types() {
        let fake = Fake::new();
        fake.pid.set(200);
        let err = type_text(&fake, "abc", &fast(), &CancelToken::new(), |_, _| {}, Some(&100)).unwrap_err();
        assert!(matches!(err, Error::Cancelled(_)));
        assert!(fake.events().is_empty());
    }

    #[test]
    fn cancel_wakes_a_long_wait() {
        let cancel = CancelToken::new();
        let other = cancel.clone();
        let started = Instant::now();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(20));
            other.cancel();
        });
        assert!(cancel.wait(Duration::from_secs(5)));
        assert!(started.elapsed() < Duration::from_secs(1));
    }
}
