//! Portable hotkey descriptions; key codes are supplied by the platform adapter.

use std::collections::BTreeSet;
use std::fmt;

use crate::error::{Error, Result};

/// macOS virtual key codes for the keys a shortcut may end with.
pub const KEY_CODES: &[(&str, u16)] = &[
    ("a", 0),
    ("s", 1),
    ("d", 2),
    ("f", 3),
    ("h", 4),
    ("g", 5),
    ("z", 6),
    ("x", 7),
    ("c", 8),
    ("v", 9),
    ("b", 11),
    ("q", 12),
    ("w", 13),
    ("e", 14),
    ("r", 15),
    ("y", 16),
    ("t", 17),
    ("1", 18),
    ("2", 19),
    ("3", 20),
    ("4", 21),
    ("6", 22),
    ("5", 23),
    ("9", 25),
    ("7", 26),
    ("8", 28),
    ("0", 29),
    ("o", 31),
    ("u", 32),
    ("i", 34),
    ("p", 35),
    ("l", 37),
    ("j", 38),
    ("k", 40),
    ("n", 45),
    ("m", 46),
    ("esc", 53),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Modifier {
    Ctrl,
    Alt,
    /// Command on macOS, the Windows key on Windows.
    Cmd,
    Shift,
}

impl Modifier {
    fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "ctrl" => Modifier::Ctrl,
            "alt" => Modifier::Alt,
            "cmd" => Modifier::Cmd,
            "shift" => Modifier::Shift,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Modifier::Ctrl => "ctrl",
            Modifier::Alt => "alt",
            Modifier::Cmd => "cmd",
            Modifier::Shift => "shift",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Hotkey {
    pub modifiers: BTreeSet<Modifier>,
    pub key: String,
}

impl Hotkey {
    pub fn parse(value: &str) -> Result<Self> {
        // Command and Windows refer to the platform's meta key. Normalizing aliases
        // also prevents registering duplicate shortcuts under different spellings.
        let value = value.to_lowercase().replace("win", "cmd").replace(' ', "");
        let mut parts: Vec<&str> = value.split('+').collect();
        let key = parts.pop().unwrap_or_default();
        let unique: BTreeSet<&str> = parts.iter().copied().collect();
        if key_code(key).is_none() || parts.is_empty() || unique.len() != parts.len() {
            return Err(Error::invalid("Пример сочетания: ctrl+alt+cmd+4"));
        }
        let modifiers = parts
            .iter()
            .map(|name| Modifier::parse(name))
            .collect::<Option<BTreeSet<_>>>()
            .ok_or_else(|| Error::invalid("Модификаторы: ctrl, alt, cmd (или win), shift"))?;
        if modifiers.iter().all(|modifier| *modifier == Modifier::Shift) {
            return Err(Error::invalid("Добавьте ctrl, alt или cmd к сочетанию"));
        }
        Ok(Hotkey { modifiers, key: key.to_string() })
    }

    pub fn key_code(&self) -> u16 {
        key_code(&self.key).expect("validated key")
    }
}

impl fmt::Display for Hotkey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for modifier in &self.modifiers {
            write!(f, "{}+", modifier.as_str())?;
        }
        f.write_str(&self.key)
    }
}

pub fn key_code(key: &str) -> Option<u16> {
    KEY_CODES.iter().find(|(name, _)| *name == key).map(|(_, code)| *code)
}
