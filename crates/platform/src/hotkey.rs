//! Shortcut strings from settings ("ctrl+alt+cmd+4") and their global registration.
use crate::{Error, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct Modifiers {
    pub ctrl: bool,
    pub alt: bool,
    pub shift: bool,
    /// Command on macOS, the Windows key on Windows.
    pub cmd: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Key {
    /// A lowercase ASCII letter or digit.
    Char(char),
    Escape,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Shortcut {
    pub modifiers: Modifiers,
    pub key: Key,
}

const EXAMPLE: &str = "Пример сочетания: ctrl+alt+cmd+4";

/// Parse a settings value. Accepts the same spellings as the Python app.
pub fn parse_shortcut(value: &str) -> Result<Shortcut> {
    // Command and Windows refer to the platform's meta key. Normalizing aliases
    // also prevents registering duplicate shortcuts under different spellings.
    let value = value.to_lowercase().replace("win", "cmd").replace(' ', "");
    let mut parts: Vec<&str> = value.split('+').collect();
    let key = match parts.pop().unwrap_or_default() {
        "esc" => Key::Escape,
        k if k.len() == 1
            && k.chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()) =>
        {
            Key::Char(k.chars().next().unwrap())
        }
        _ => return Err(Error::Operation(EXAMPLE.into())),
    };
    if parts.is_empty() {
        return Err(Error::Operation(EXAMPLE.into()));
    }
    let mut modifiers = Modifiers::default();
    for part in parts {
        let slot = match part {
            "ctrl" => &mut modifiers.ctrl,
            "alt" => &mut modifiers.alt,
            "shift" => &mut modifiers.shift,
            "cmd" => &mut modifiers.cmd,
            _ => {
                return Err(Error::Operation(
                    "Модификаторы: ctrl, alt, cmd (или win), shift".into(),
                ))
            }
        };
        if std::mem::replace(slot, true) {
            return Err(Error::Operation(EXAMPLE.into()));
        }
    }
    if !(modifiers.ctrl || modifiers.alt || modifiers.cmd) {
        return Err(Error::Operation(
            "Добавьте ctrl, alt или cmd к сочетанию".into(),
        ));
    }
    Ok(Shortcut { modifiers, key })
}

#[cfg(any(windows, target_os = "macos"))]
pub use native::Hotkeys;

#[cfg(any(windows, target_os = "macos"))]
mod native {
    use super::{parse_shortcut, Key, Shortcut};
    use crate::{Error, Result};
    use global_hotkey::hotkey::{Code, HotKey, Modifiers as Mods};
    use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};

    /// Global shortcuts: `RegisterHotKey` on Windows, Carbon `RegisterEventHotKey`
    /// on macOS (no Input Monitoring permission needed).
    ///
    /// Create it on the thread that runs the GUI event loop (the main thread on
    /// macOS); pressed shortcuts arrive through `GlobalHotKeyEvent::receiver()`
    /// or `GlobalHotKeyEvent::set_event_handler`, and [`Hotkeys::action`] maps
    /// them back to the caller's action.
    pub struct Hotkeys<A> {
        manager: GlobalHotKeyManager,
        registered: Vec<(HotKey, A)>,
    }

    impl<A: Clone> Hotkeys<A> {
        pub fn new() -> Result<Self> {
            let manager = GlobalHotKeyManager::new()
                .map_err(|e| Error::Operation(format!("Горячие клавиши недоступны: {e}")))?;
            Ok(Self {
                manager,
                registered: Vec::new(),
            })
        }

        /// Replace all registrations. Either every binding is registered or none.
        pub fn register(&mut self, bindings: &[(A, &str)]) -> Result<()> {
            self.unregister_all();
            for (action, value) in bindings {
                let hotkey = to_hotkey(parse_shortcut(value)?);
                if self.manager.register(hotkey).is_err() {
                    self.unregister_all();
                    return Err(Error::Operation(format!(
                        "Сочетание {value} занято или недоступно. Измените его в разделе «Система»."
                    )));
                }
                self.registered.push((hotkey, action.clone()));
            }
            Ok(())
        }

        pub fn unregister_all(&mut self) {
            for (hotkey, _) in self.registered.drain(..) {
                let _ = self.manager.unregister(hotkey);
            }
        }

        /// The action for a key press; releases and foreign ids return `None`.
        pub fn action(&self, event: &GlobalHotKeyEvent) -> Option<A> {
            if event.state() != HotKeyState::Pressed {
                return None;
            }
            self.registered
                .iter()
                .find(|(h, _)| h.id() == event.id())
                .map(|(_, a)| a.clone())
        }
    }

    impl<A> Drop for Hotkeys<A> {
        fn drop(&mut self) {
            for (hotkey, _) in self.registered.drain(..) {
                let _ = self.manager.unregister(hotkey);
            }
        }
    }

    fn to_hotkey(shortcut: Shortcut) -> HotKey {
        let m = shortcut.modifiers;
        let mut mods = Mods::empty();
        for (on, flag) in [
            (m.ctrl, Mods::CONTROL),
            (m.alt, Mods::ALT),
            (m.shift, Mods::SHIFT),
            (m.cmd, Mods::META),
        ] {
            if on {
                mods |= flag;
            }
        }
        HotKey::new(Some(mods), code(shortcut.key))
    }

    fn code(key: Key) -> Code {
        const LETTERS: [Code; 26] = [
            Code::KeyA,
            Code::KeyB,
            Code::KeyC,
            Code::KeyD,
            Code::KeyE,
            Code::KeyF,
            Code::KeyG,
            Code::KeyH,
            Code::KeyI,
            Code::KeyJ,
            Code::KeyK,
            Code::KeyL,
            Code::KeyM,
            Code::KeyN,
            Code::KeyO,
            Code::KeyP,
            Code::KeyQ,
            Code::KeyR,
            Code::KeyS,
            Code::KeyT,
            Code::KeyU,
            Code::KeyV,
            Code::KeyW,
            Code::KeyX,
            Code::KeyY,
            Code::KeyZ,
        ];
        const DIGITS: [Code; 10] = [
            Code::Digit0,
            Code::Digit1,
            Code::Digit2,
            Code::Digit3,
            Code::Digit4,
            Code::Digit5,
            Code::Digit6,
            Code::Digit7,
            Code::Digit8,
            Code::Digit9,
        ];
        match key {
            Key::Escape => Code::Escape,
            Key::Char(c @ 'a'..='z') => LETTERS[(c as u8 - b'a') as usize],
            Key::Char(c) => DIGITS[(c as u8 - b'0') as usize],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_defaults_and_aliases() {
        let s = parse_shortcut("ctrl+alt+cmd+4").unwrap();
        assert_eq!(s.key, Key::Char('4'));
        assert!(s.modifiers.ctrl && s.modifiers.alt && s.modifiers.cmd && !s.modifiers.shift);
        assert_eq!(
            parse_shortcut("Ctrl + Win + V").unwrap(),
            parse_shortcut("ctrl+cmd+v").unwrap()
        );
        assert_eq!(parse_shortcut("ctrl+alt+esc").unwrap().key, Key::Escape);
    }

    #[test]
    fn rejects_invalid_values() {
        for bad in [
            "v",
            "ctrl+",
            "ctrl+f1",
            "shift+v",
            "ctrl+ctrl+v",
            "hyper+v",
            "ctrl+alt+vv",
            "",
        ] {
            assert!(parse_shortcut(bad).is_err(), "{bad}");
        }
    }
}
