//! Cancellation and optional technical deadlines, separate from speed metrics.

use std::time::Duration;

use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::error::{Error, Result, seconds};

/// One user-visible operation (OCR, review, preparation). Cloning shares the
/// cancellation state, so a controller can cancel work running elsewhere.
#[derive(Debug, Clone)]
pub struct Operation {
    started: Instant,
    budget: Option<Duration>,
    token: CancellationToken,
}

impl Default for Operation {
    fn default() -> Self {
        Self::new()
    }
}

impl Operation {
    pub fn new() -> Self {
        Self { started: Instant::now(), budget: None, token: CancellationToken::new() }
    }

    pub fn with_budget(budget: Duration) -> Self {
        Self { budget: Some(budget), ..Self::new() }
    }

    /// Shares an existing cancellation token, e.g. one owned by a model preparation.
    pub fn with_token(mut self, token: CancellationToken) -> Self {
        self.token = token;
        self
    }

    /// Backdates the start, for operations whose clock began before this object.
    pub fn started_at(mut self, started: Instant) -> Self {
        self.started = started;
        self
    }

    pub fn budget(&self) -> Option<Duration> {
        self.budget
    }

    pub fn token(&self) -> &CancellationToken {
        &self.token
    }

    pub fn elapsed(&self) -> Duration {
        self.started.elapsed()
    }

    pub fn cancel(&self) {
        self.token.cancel();
    }

    pub fn is_cancelled(&self) -> bool {
        self.token.is_cancelled()
    }

    /// Fails when cancelled or past the budget; returns the remaining budget.
    pub fn check(&self) -> Result<Option<Duration>> {
        if self.token.is_cancelled() {
            return Err(Error::cancelled());
        }
        let Some(budget) = self.budget else {
            return Ok(None);
        };
        let remaining = budget.checked_sub(self.started.elapsed()).filter(|d| !d.is_zero());
        match remaining {
            Some(remaining) => Ok(Some(remaining)),
            None if budget == Duration::from_secs(4) => {
                Err(Error::DeadlineExceeded("OCR превысил 4 секунды. Буфер не изменён.".into()))
            }
            None => {
                Err(Error::DeadlineExceeded(format!("Операция превысила {} с. Буфер не изменён.", seconds(budget))))
            }
        }
    }

    /// Resolves when the operation is cancelled or its budget runs out, with the
    /// error `check` would report. Never resolves for an unbounded, live operation.
    pub async fn interrupted(&self) -> Error {
        match self.budget {
            Some(budget) => {
                tokio::select! {
                    _ = self.token.cancelled() => {}
                    _ = tokio::time::sleep_until(self.started + budget) => {}
                }
            }
            None => self.token.cancelled().await,
        }
        self.check().err().unwrap_or_else(Error::cancelled)
    }

    /// Runs `future` until it finishes or the operation is interrupted. Dropping
    /// the future on interruption also closes any socket it owns.
    pub async fn run<T>(&self, future: impl Future<Output = Result<T>>) -> Result<T> {
        self.check()?;
        tokio::select! {
            biased;
            error = self.interrupted() => Err(error),
            result = future => {
                self.check()?;
                result
            }
        }
    }

    /// Writes the result only if the operation is still live; returns the elapsed time.
    pub fn publish(&self, text: &str, write: impl FnOnce(&str)) -> Result<Duration> {
        self.check()?;
        if text.trim().is_empty() {
            return Err(Error::operation("Текст не найден. Буфер не изменён."));
        }
        write(text);
        let elapsed = self.started.elapsed();
        if let Some(budget) = self.budget.filter(|budget| elapsed > *budget) {
            // The clipboard write itself cannot be rolled back atomically.
            return Err(Error::DeadlineExceeded(format!(
                "Запись в буфер превысила технический срок {} с.",
                seconds(budget)
            )));
        }
        Ok(elapsed)
    }
}
