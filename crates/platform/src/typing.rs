//! Cancellable typing policy shared by the native keyboards.
//!
//! The full window identity (an AX query on macOS) is read once at the start
//! of a run and again after each newline. Before every keystroke only the cheap
//! focus check runs (`GetForegroundWindow` / the frontmost pid).
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use crate::hotkey::Shortcut;
use crate::{Error, Result};

pub const FOCUS_CHANGED: &str = "Пауза: активное приложение или окно изменилось";
const STOPPED: &str = "Печать остановлена";
pub const MODIFIER_PRESSED: &str = "Пауза: нажата клавиша-модификатор";
pub const OWN_WINDOW: &str = "Активно окно ctrlCctrlV · переключитесь в окно, куда печатать";
const LAYOUT_REFUSED: &str = "Окно не переключило раскладку клавиатуры";
const NO_LAYOUT_SHORTCUT: &str = "Для этой раскладки нет сочетания переключения: доступны латиница и кириллица";
/// How long the focused window has to apply a requested layout switch.
const LAYOUT_SWITCH_TIMEOUT: Duration = Duration::from_millis(500);

/// How the window gets a layout that types the next character.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LayoutSwitch {
    /// Ask the window to switch (`WM_INPUTLANGCHANGEREQUEST`); a remote session
    /// follows only when its client synchronizes the layout.
    #[default]
    Sync,
    /// Press the shortcut the remote session assigns to each language, so the
    /// remote switches itself. The local layout stays as it is.
    Hotkeys { latin: Shortcut, cyrillic: Shortcut },
}

/// Which of the per-language shortcuts switches to `layout`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Script {
    Latin,
    Cyrillic,
}

/// How characters reach the focused window.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TypingMethod {
    /// Each character as a Unicode code point, independent of the keyboard layout.
    #[default]
    Unicode,
    /// The physical keys that type each character in an installed keyboard
    /// layout, switching the window's layout when needed. Remote desktop and
    /// VDI clients forward only real keys and turn Unicode input into `?`.
    Layout,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TypingSettings {
    pub character_delay: Duration,
    pub newline_delay: Duration,
    /// Select the editor's automatic indentation (Shift+Home) after each newline
    /// so the next typed character replaces it.
    pub autoindent: bool,
    /// Type this many spaces for a tab; 0 sends the Tab key.
    pub spaces_per_tab: u8,
    pub method: TypingMethod,
    pub layout_switch: LayoutSwitch,
    /// Pause after switching the layout, so a remote session can follow it.
    pub layout_delay: Duration,
    /// Press Delete after an opening bracket or quote, removing the closing
    /// one an editor inserts by itself.
    pub delete_autoclose: bool,
}

impl Default for TypingSettings {
    fn default() -> Self {
        Self {
            character_delay: Duration::from_millis(8),
            newline_delay: Duration::from_millis(60),
            autoindent: false,
            spaces_per_tab: 0,
            method: TypingMethod::Unicode,
            layout_switch: LayoutSwitch::Sync,
            layout_delay: Duration::from_millis(150),
            delete_autoclose: false,
        }
    }
}

/// Native keystroke emulation. Implementations post events to the focused window.
pub trait Keyboard {
    /// Identity of the focused window, compared to detect a focus change.
    type Target: PartialEq + Clone;

    fn accessibility_granted(&self) -> bool;
    fn modifiers_pressed(&self) -> bool;
    /// Full identity of the focused window. May be slow (an AX query on macOS).
    fn focused_target(&self) -> Option<Self::Target>;
    /// Cheap check that `target`'s application or window still owns focus.
    fn still_focused(&self, target: &Self::Target) -> bool;
    /// Whether `target` is one of this app's own windows, which are never typed into.
    fn owned_by_this_app(&self, _target: &Self::Target) -> bool {
        false
    }
    fn character(&self, ch: char) -> Result<()>;
    /// The keyboard layout of the focused window, as an opaque id. `None` where
    /// typing with layout keys is not supported.
    fn layout(&self) -> Option<isize> {
        None
    }
    /// An installed layout that types `ch` with its keys, `current` first.
    fn layout_with(&self, _ch: char, _current: isize) -> Option<isize> {
        None
    }
    /// Ask the focused window to switch to `layout`; it applies it asynchronously.
    fn switch_layout(&self, _layout: isize) -> Result<()> {
        Err(Error::Operation(LAYOUT_REFUSED.into()))
    }
    /// The script `layout` types, when it is one with a switching shortcut.
    fn layout_script(&self, _layout: isize) -> Option<Script> {
        None
    }
    /// Press and release a shortcut, as real keys.
    fn shortcut(&self, _shortcut: &Shortcut) -> Result<()> {
        Ok(())
    }
    /// The forward-delete key.
    fn delete(&self) -> Result<()> {
        Ok(())
    }
    /// Press the keys that type `ch` in `layout`.
    fn layout_character(&self, ch: char, _layout: isize) -> Result<()> {
        self.character(ch)
    }
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
        let (guard, _) =
            wake.wait_timeout_while(guard, timeout, |cancelled| !*cancelled).unwrap_or_else(|e| e.into_inner());
        *guard
    }
}

/// The characters a run types: line breaks normalized to `\n` and, when
/// `spaces_per_tab` is set, tabs expanded so every typed space has its own
/// position to resume from.
pub fn prepare(text: &str, settings: &TypingSettings) -> Vec<char> {
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    if settings.spaces_per_tab == 0 {
        return text.chars().collect();
    }
    let tab = " ".repeat(settings.spaces_per_tab as usize);
    text.replace('\t', &tab).chars().collect()
}

/// Type `chars[start..]` into the focused window. Returns the position reached
/// (the index of the next character to type) with the outcome, so a stopped
/// run can be resumed from there, in the same or another window.
///
/// `expected` is the window that was focused when typing was requested; if
/// focus moved before the run starts nothing is typed.
pub fn type_chars<K: Keyboard>(
    keyboard: &K,
    chars: &[char],
    start: usize,
    settings: &TypingSettings,
    cancel: &CancelToken,
    mut progress: impl FnMut(usize, usize),
    expected: Option<&K::Target>,
) -> (usize, Result<()>) {
    let mut state = RunState { position: start.min(chars.len()), target: None, original: None, current: None };
    let result = run(keyboard, chars, &mut state, settings, cancel, &mut progress, expected);
    state.restore(keyboard, settings);
    (state.position, result)
}

/// Where a run is, with the window it types into and that window's layout
/// when the run started and now.
struct RunState<T> {
    position: usize,
    target: Option<T>,
    original: Option<isize>,
    current: Option<isize>,
}

impl<T> RunState<T> {
    /// Give the window back the layout it had, unless focus has left it.
    fn restore<K: Keyboard<Target = T>>(&self, keyboard: &K, settings: &TypingSettings) {
        if settings.layout_switch != LayoutSwitch::Sync {
            // The remote session switched; which layout it had is unknown.
            return;
        }
        if let (Some(target), Some(original), Some(current)) = (&self.target, self.original, self.current)
            && original != current
            && keyboard.still_focused(target)
        {
            let _ = keyboard.switch_layout(original);
        }
    }
}

/// Which typed opening brackets and quotes an editor pairs by itself, by the
/// rules VS Code and CodeMirror share: brackets always, quotes only outside
/// strings and comments and not right after a word character, and no quote
/// after a backslash. Tracks one line; a newline resets it.
#[derive(Debug, Default)]
struct AutoClose {
    /// The quote that opened the string the cursor is in.
    string: Option<char>,
    comment: bool,
    escaped: bool,
    /// The previous character on the line, for `//`.
    previous: Option<char>,
}

impl AutoClose {
    /// Whether typing `ch` makes the editor insert a closing character.
    /// `before` is the character typed before `ch`, on this line or not.
    fn opens(&mut self, ch: char, before: Option<&char>) -> bool {
        let escaped = std::mem::replace(&mut self.escaped, false);
        let previous = self.previous.replace(ch);
        if matches!(ch, '(' | '[' | '{') {
            return true;
        }
        if self.comment {
            return false;
        }
        if let Some(quote) = self.string {
            if ch == '\\' && !escaped {
                self.escaped = true;
            } else if ch == quote && !escaped {
                self.string = None;
            }
            return false;
        }
        match ch {
            '"' | '\'' | '`' => {
                if before.is_some_and(|c| c.is_alphanumeric() || *c == '_') {
                    return false;
                }
                self.string = Some(ch);
                true
            }
            '#' => {
                self.comment = true;
                false
            }
            '/' if previous == Some('/') => {
                self.comment = true;
                false
            }
            _ => false,
        }
    }
}

fn run<K: Keyboard>(
    keyboard: &K,
    chars: &[char],
    state: &mut RunState<K::Target>,
    settings: &TypingSettings,
    cancel: &CancelToken,
    progress: &mut impl FnMut(usize, usize),
    expected: Option<&K::Target>,
) -> Result<()> {
    if !keyboard.accessibility_granted() {
        return Err(Error::Operation("Разрешите управление вводом в разделе «Система»".into()));
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
    if keyboard.owned_by_this_app(&target) {
        return Err(Error::Cancelled(OWN_WINDOW.into()));
    }
    state.target = Some(target.clone());

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
    if settings.method == TypingMethod::Layout {
        // With shortcuts the remote layout is unknown until the first switch.
        state.original = keyboard.layout();
        state.current = if settings.layout_switch == LayoutSwitch::Sync { state.original } else { None };
    }

    let character = |ch: char, state: &mut RunState<K::Target>| -> Result<()> {
        if settings.method != TypingMethod::Layout {
            return keyboard.character(ch);
        }
        let preferred = state.current.or(state.original).unwrap_or_default();
        let Some(layout) = keyboard.layout_with(ch, preferred) else {
            // No installed layout has it (an emoji): only Unicode input can type it.
            return keyboard.character(ch);
        };
        if state.current != Some(layout) {
            match settings.layout_switch {
                LayoutSwitch::Sync => {
                    keyboard.switch_layout(layout)?;
                    let deadline = Instant::now() + LAYOUT_SWITCH_TIMEOUT;
                    while keyboard.layout() != Some(layout) {
                        check()?;
                        if Instant::now() >= deadline {
                            return Err(Error::Operation(LAYOUT_REFUSED.into()));
                        }
                        cancel.wait(Duration::from_millis(10));
                    }
                }
                LayoutSwitch::Hotkeys { latin, cyrillic } => {
                    let shortcut = match keyboard.layout_script(layout) {
                        Some(Script::Latin) => latin,
                        Some(Script::Cyrillic) => cyrillic,
                        None => return Err(Error::Operation(NO_LAYOUT_SHORTCUT.into())),
                    };
                    keyboard.shortcut(&shortcut)?;
                }
            }
            state.current = Some(layout);
            cancel.wait(settings.layout_delay);
            check()?;
        }
        keyboard.layout_character(ch, layout)
    };

    let mut autoclose = AutoClose::default();
    // A run that stopped right after a newline resumes by selecting the new
    // line's automatic indentation, as the newline itself would have.
    if settings.autoindent && state.position > 0 && state.position < chars.len() && chars[state.position - 1] == '\n' {
        check()?;
        keyboard.select_indentation()?;
    }
    while let Some(&ch) = chars.get(state.position) {
        let index = state.position;
        check()?;
        match ch {
            '\n' => {
                keyboard.enter()?;
                state.position = index + 1;
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
            '\t' => keyboard.tab()?,
            _ => {
                character(ch, state)?;
                if settings.delete_autoclose && autoclose.opens(ch, chars[..index].last()) {
                    cancel.wait(settings.character_delay);
                    check()?;
                    keyboard.delete()?;
                }
            }
        }
        if ch == '\n' {
            autoclose = AutoClose::default();
        }
        state.position = index + 1;
        progress(state.position, chars.len());
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
        Switch(isize),
        Key(char, isize),
        Shortcut(Shortcut),
        Delete,
    }

    const LATIN: isize = 1;
    const CYRILLIC: isize = 2;

    struct Fake {
        events: RefCell<Vec<Event>>,
        pid: Cell<u32>,
        layout: Cell<isize>,
        /// Whether the window applies a requested layout.
        switches: bool,
        full_checks: Cell<usize>,
        on_character: Option<Box<dyn Fn()>>,
    }

    impl Fake {
        fn new() -> Self {
            Self {
                events: RefCell::default(),
                pid: Cell::new(100),
                layout: Cell::new(LATIN),
                switches: true,
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
        fn owned_by_this_app(&self, target: &u32) -> bool {
            *target == 1
        }
        fn character(&self, ch: char) -> Result<()> {
            self.events.borrow_mut().push(Event::Text(ch));
            if let Some(hook) = &self.on_character {
                hook();
            }
            Ok(())
        }
        fn layout(&self) -> Option<isize> {
            Some(self.layout.get())
        }
        fn layout_with(&self, ch: char, current: isize) -> Option<isize> {
            // Digits and punctuation are in both layouts.
            let has = |layout| match ch {
                'а'..='я' | 'А'..='Я' | 'ё' | 'Ё' => layout == CYRILLIC,
                'a'..='z' | 'A'..='Z' => layout == LATIN,
                _ => ch.is_ascii(),
            };
            [current, LATIN, CYRILLIC].into_iter().find(|&layout| has(layout))
        }
        fn switch_layout(&self, layout: isize) -> Result<()> {
            self.events.borrow_mut().push(Event::Switch(layout));
            if self.switches {
                self.layout.set(layout);
            }
            Ok(())
        }
        fn layout_character(&self, ch: char, layout: isize) -> Result<()> {
            self.events.borrow_mut().push(Event::Key(ch, layout));
            Ok(())
        }
        fn layout_script(&self, layout: isize) -> Option<Script> {
            match layout {
                LATIN => Some(Script::Latin),
                CYRILLIC => Some(Script::Cyrillic),
                _ => None,
            }
        }
        fn shortcut(&self, shortcut: &Shortcut) -> Result<()> {
            self.events.borrow_mut().push(Event::Shortcut(*shortcut));
            Ok(())
        }
        fn delete(&self) -> Result<()> {
            self.events.borrow_mut().push(Event::Delete);
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

    fn type_text(
        fake: &Fake,
        text: &str,
        settings: &TypingSettings,
        cancel: &CancelToken,
        progress: impl FnMut(usize, usize),
        expected: Option<&u32>,
    ) -> Result<()> {
        type_chars(fake, &prepare(text, settings), 0, settings, cancel, progress, expected).1
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
        let settings = TypingSettings { autoindent: true, spaces_per_tab: 2, ..fast() };
        type_text(&fake, "Ж\r\n\t🙂\n", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        use Event::*;
        assert_eq!(fake.events(), [Text('Ж'), Enter, Indent, Text(' '), Text(' '), Text('🙂'), Enter]);
    }

    #[test]
    fn layout_keys_switch_layouts_and_restore_the_original() {
        let fake = Fake::new();
        let settings = TypingSettings { method: TypingMethod::Layout, layout_delay: Duration::ZERO, ..fast() };
        type_text(&fake, "Да, ok 1🙂", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        use Event::*;
        let events = [
            Switch(CYRILLIC),
            Key('Д', CYRILLIC),
            Key('а', CYRILLIC),
            Key(',', CYRILLIC),
            Key(' ', CYRILLIC),
            Switch(LATIN),
            Key('o', LATIN),
            Key('k', LATIN),
            Key(' ', LATIN),
            Key('1', LATIN),
            Text('🙂'),
        ];
        assert_eq!(fake.events(), events);
        assert_eq!(fake.layout.get(), LATIN);

        let fake = Fake::new();
        type_text(&fake, "Я", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        assert_eq!(fake.events(), [Switch(CYRILLIC), Key('Я', CYRILLIC), Switch(LATIN)]);
        assert_eq!(fake.layout.get(), LATIN);
    }

    #[test]
    fn layout_keys_stop_when_the_window_keeps_its_layout() {
        let fake = Fake { switches: false, ..Fake::new() };
        let settings = TypingSettings { method: TypingMethod::Layout, ..fast() };
        let err = type_text(&fake, "aЖ", &settings, &CancelToken::new(), |_, _| {}, None).unwrap_err();
        assert_eq!(err, Error::Operation(LAYOUT_REFUSED.into()));
        assert_eq!(fake.events(), [Event::Key('a', LATIN), Event::Switch(CYRILLIC)]);
    }

    #[test]
    fn layout_shortcuts_switch_the_remote_without_touching_the_local_layout() {
        use crate::hotkey::parse_shortcut;
        let fake = Fake::new();
        let (latin, cyrillic) = (parse_shortcut("ctrl+shift+1").unwrap(), parse_shortcut("ctrl+shift+2").unwrap());
        let settings = TypingSettings {
            method: TypingMethod::Layout,
            layout_switch: LayoutSwitch::Hotkeys { latin, cyrillic },
            layout_delay: Duration::ZERO,
            ..fast()
        };
        // The remote layout is unknown, so even Latin text starts with its shortcut.
        type_text(&fake, "a=Я", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        use Event::*;
        let events = [Shortcut(latin), Key('a', LATIN), Key('=', LATIN), Shortcut(cyrillic), Key('Я', CYRILLIC)];
        assert_eq!(fake.events(), events);
        assert_eq!(fake.layout.get(), LATIN);
    }

    #[test]
    fn deletes_the_closing_characters_an_editor_adds() {
        let fake = Fake::new();
        let settings = TypingSettings { delete_autoclose: true, ..fast() };
        type_text(&fake, "f(\"a\")", &settings, &CancelToken::new(), |_, _| {}, None).unwrap();
        use Event::*;
        let events = [Text('f'), Text('('), Delete, Text('"'), Delete, Text('a'), Text('"'), Text(')')];
        assert_eq!(fake.events(), events);
    }

    #[test]
    fn autoclose_follows_strings_comments_and_words() {
        let mut auto = AutoClose::default();
        let mut line = |text: &str| -> Vec<bool> {
            auto = AutoClose::default();
            let chars: Vec<char> = text.chars().collect();
            (0..chars.len()).map(|i| auto.opens(chars[i], chars[..i].last())).collect()
        };
        // An apostrophe inside a word and a quote inside a string are not paired.
        assert_eq!(line("don't"), [false, false, false, false, false]);
        assert_eq!(line("'a\"'"), [true, false, false, false]);
        // Escapes do not end the string; brackets pair even inside strings.
        assert_eq!(line("'\\'(' "), [true, false, false, true, false, false]);
        assert_eq!(line("\"\"\""), [true, false, true]);
        // Comments pair brackets but not quotes.
        assert_eq!(line("# it's ("), [false, false, false, false, false, false, false, true]);
        assert_eq!(line("// a'"), [false, false, false, false, false]);
        assert_eq!(line("a/'"), [false, false, true]);
    }

    #[test]
    fn unicode_method_ignores_layouts() {
        let fake = Fake::new();
        type_text(&fake, "Жa", &fast(), &CancelToken::new(), |_, _| {}, None).unwrap();
        assert_eq!(fake.events(), [Event::Text('Ж'), Event::Text('a')]);
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
        let err = type_text(&fake, "abc", &fast(), &CancelToken::new(), |_, _| fake.pid.set(200), None).unwrap_err();
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
        let settings = TypingSettings { spaces_per_tab: 4, ..fast() };
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
    fn resumes_from_where_focus_left_in_another_window() {
        let fake = Fake::new();
        let chars = prepare("abcd", &fast());
        let (position, result) = type_chars(
            &fake,
            &chars,
            0,
            &fast(),
            &CancelToken::new(),
            |done, _| {
                if done == 2 {
                    fake.pid.set(200)
                }
            },
            None,
        );
        assert!(matches!(result, Err(Error::Cancelled(_))));
        assert_eq!(position, 2);
        let (position, result) = type_chars(&fake, &chars, position, &fast(), &CancelToken::new(), |_, _| {}, None);
        result.unwrap();
        assert_eq!(position, 4);
        use Event::*;
        assert_eq!(fake.events(), [Text('a'), Text('b'), Text('c'), Text('d')]);
    }

    #[test]
    fn resume_after_a_newline_selects_the_indentation_first() {
        let fake = Fake::new();
        let settings = TypingSettings { autoindent: true, ..fast() };
        let chars = prepare("a\nb", &settings);
        let (position, result) = type_chars(&fake, &chars, 2, &settings, &CancelToken::new(), |_, _| {}, None);
        result.unwrap();
        assert_eq!(position, 3);
        assert_eq!(fake.events(), [Event::Indent, Event::Text('b')]);
    }

    #[test]
    fn never_types_into_its_own_window() {
        let fake = Fake::new();
        fake.pid.set(1);
        let err = type_text(&fake, "abc", &fast(), &CancelToken::new(), |_, _| {}, None).unwrap_err();
        assert_eq!(err, Error::Cancelled(OWN_WINDOW.into()));
        assert!(fake.events().is_empty());
    }

    #[test]
    fn prepare_expands_tabs_and_line_breaks() {
        let settings = TypingSettings { spaces_per_tab: 2, ..fast() };
        assert_eq!(prepare("\ta\r\nb\r", &settings), [' ', ' ', 'a', '\n', 'b', '\n']);
        assert_eq!(prepare("\t", &fast()), ['\t']);
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
