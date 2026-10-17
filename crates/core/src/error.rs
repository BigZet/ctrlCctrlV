//! User-facing failures. Messages are shown to the user verbatim, so they
//! never include provider responses, screenshots or keys.

use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum Error {
    /// The user cancelled; nothing was published.
    #[error("{0}")]
    Cancelled(String),
    /// A technical deadline passed; nothing was published.
    #[error("{0}")]
    DeadlineExceeded(String),
    /// Preparation may be retried after releasing another owned model.
    #[error("{0}")]
    InsufficientMemory(String),
    #[error("{0}")]
    Operation(String),
    /// Invalid input or configuration.
    #[error("{0}")]
    Invalid(String),
}

pub type Result<T, E = Error> = std::result::Result<T, E>;

impl Error {
    pub fn cancelled() -> Self {
        Error::Cancelled("Операция отменена".into())
    }

    pub fn operation(message: impl Into<String>) -> Self {
        Error::Operation(message.into())
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        Error::Invalid(message.into())
    }

    pub fn is_cancelled(&self) -> bool {
        matches!(self, Error::Cancelled(_))
    }
}

/// Seconds formatted like Python's `{value:g}` for the values used in messages.
pub(crate) fn seconds(value: Duration) -> String {
    let value = value.as_secs_f64();
    let text = format!("{value:.6}");
    text.trim_end_matches('0').trim_end_matches('.').to_string()
}
