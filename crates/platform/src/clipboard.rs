//! System clipboard text.
use crate::{Error, Result};

pub struct Clipboard(arboard::Clipboard);

fn failed(e: arboard::Error) -> Error {
    Error::Operation(format!("Буфер обмена недоступен: {e}"))
}

impl Clipboard {
    pub fn new() -> Result<Self> {
        arboard::Clipboard::new().map(Self).map_err(failed)
    }

    /// Clipboard text; an empty or non-text clipboard is an error.
    pub fn text(&mut self) -> Result<String> {
        match self.0.get_text() {
            Ok(text) if !text.is_empty() => Ok(text),
            Ok(_) | Err(arboard::Error::ContentNotAvailable) => {
                Err(Error::Operation("В буфере обмена нет текста".into()))
            }
            Err(e) => Err(failed(e)),
        }
    }

    pub fn set_text(&mut self, text: &str) -> Result<()> {
        self.0.set_text(text).map_err(failed)
    }
}
