//! Portable shell logic: what the app is doing, the payloads sent to the web
//! UI, and how desktop monitors map to capture monitors. Kept free of Tauri so
//! the tests run on any host.
use std::sync::Arc;
use std::time::{Duration, Instant};

use ctrlcctrlv_core::settings::Settings;
use ctrlcctrlv_platform::typing::{TypingMethod, TypingSettings, prepare};
use serde::{Deserialize, Serialize};

/// Event names shared with `ui/*.js`.
pub mod events {
    /// [`super::Status`]: the app's phase and the last message.
    pub const STATUS: &str = "status";
    /// [`super::SessionView`]: a new capture opened in the result window.
    pub const SESSION: &str = "ocr-session";
    /// [`super::RunView`]: a model's answer for the capture, sent again while it streams.
    pub const RUN: &str = "ocr-run";
    /// [`super::TypingView`]: the typing job's state and progress, for the panel and the main window.
    pub const TYPING: &str = "typing";
    /// [`super::Appended`]: text recognized in one more region, to add to the result.
    pub const APPENDED: &str = "ocr-appended";
    /// Sent to the overlays right before they are shown, to clear the last selection.
    pub const OVERLAY_RESET: &str = "overlay-reset";
    /// [`super::InstallProgress`] of a model download.
    pub const INSTALL: &str = "model-install";
    /// Models, the engine or settings changed; pages reload what they show.
    pub const CHANGED: &str = "changed";
}

pub const OPENROUTER_ENDPOINT: &str = "https://openrouter.ai/api/v1/chat/completions";

/// Capture and typing never run at the same time; the phase says which one owns the app.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Idle,
    Selecting,
    Recognizing,
    Typing,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Status {
    pub phase: Phase,
    pub message: String,
    pub error: bool,
}

impl Status {
    pub fn new(phase: Phase, message: impl Into<String>) -> Self {
        Self { phase, message: message.into(), error: false }
    }

    pub fn error(phase: Phase, message: impl Into<String>) -> Self {
        Self { phase, message: message.into(), error: true }
    }
}

/// A model to run on a capture, as picked in the result window.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ModelChoice {
    /// `local` or `openrouter`, as stored in history.
    pub provider: String,
    pub model: String,
}

impl ModelChoice {
    pub fn local(&self) -> bool {
        self.provider == "local"
    }
}

/// Recognize the image again, or check a transcription against it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RunKind {
    Ocr,
    Review,
    /// Recognition of one more region, whose text goes after the result.
    Append,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RunState {
    Waiting,
    Running,
    Done,
    Failed,
    Cancelled,
}

/// One model's answer for a capture. A capture keeps its image, so later runs
/// (other models, re-checks of an edited text) arrive with the same `capture`;
/// runs started together share a `round`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunView {
    pub id: u64,
    pub capture: u64,
    pub round: u32,
    pub kind: RunKind,
    pub provider: String,
    pub model: String,
    pub label: String,
    pub state: RunState,
    /// The answer, or the part received so far while running.
    pub text: String,
    /// The text a review started from, to compare the proposal with.
    pub base: Option<String>,
    pub error: Option<String>,
    pub elapsed_ms: u64,
}

/// The capture shown in the result window.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionView {
    pub capture: u64,
    /// `data:image/png;base64,...`, or `None` for an entry opened from history.
    pub image: Option<String>,
    pub text: String,
    pub runs: Vec<RunView>,
}

/// Cloud models run in parallel; local ones one after another, since each
/// needs the engine to itself. Duplicates are dropped, order is kept.
pub fn split_choices(choices: &[ModelChoice]) -> (Vec<ModelChoice>, Vec<ModelChoice>) {
    let mut seen = std::collections::HashSet::new();
    let unique: Vec<ModelChoice> = choices.iter().filter(|c| seen.insert((*c).clone())).cloned().collect();
    unique.into_iter().partition(|choice| !choice.local())
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallProgress {
    pub model: String,
    pub label: String,
    pub done: u64,
    pub total: u64,
    /// Set once the download ends; `error` says why it failed.
    pub finished: bool,
    pub error: Option<String>,
}

/// Where a typing job is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TypingState {
    /// Waiting before the first keystroke so the user can focus the target window.
    Countdown,
    Typing,
    /// Stopped part way, by the user or a focus change; can continue from `done`.
    Paused,
    Done,
    Stopped,
}

impl TypingState {
    pub fn finished(self) -> bool {
        matches!(self, TypingState::Done | TypingState::Stopped)
    }
}

/// What the typing panel shows.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TypingView {
    pub id: u64,
    pub state: TypingState,
    pub done: usize,
    pub total: usize,
    /// The line the next character goes to, 1-based, and how many lines there are.
    pub line: usize,
    pub lines: usize,
    pub message: String,
    pub error: bool,
    /// Seconds left while counting down.
    pub countdown: u64,
    /// The typing shortcut, which also continues a paused job.
    pub hotkey: String,
}

/// Why a typing run ended, as the shell saw it.
#[derive(Debug, Clone, PartialEq)]
pub enum RunEnd {
    Completed,
    /// The user asked to stop for good.
    Stopped,
    /// The user paused, focus moved or a key was pressed: keep the position.
    Interrupted {
        message: String,
        error: bool,
    },
}

/// Text being typed, with the position to continue from. It survives a pause
/// so the rest can be typed later, into the same window or another one.
#[derive(Debug, Clone)]
pub struct TypingJob {
    pub id: u64,
    /// The clipboard text as copied; pressing the typing shortcut again with
    /// the same clipboard continues the job instead of starting over.
    pub text: String,
    pub chars: Arc<Vec<char>>,
    /// Kept from the start, so the characters and their positions stay valid.
    pub settings: TypingSettings,
    pub position: usize,
    pub state: TypingState,
    pub message: String,
    pub error: bool,
    pub countdown: u64,
    /// When a pressed modifier paused the job: the shortcut that follows is that same key press.
    pub paused_by_key: Option<Instant>,
}

impl TypingJob {
    pub fn new(id: u64, text: String, settings: TypingSettings) -> Self {
        let chars = Arc::new(prepare(&text, &settings));
        Self {
            id,
            text,
            chars,
            settings,
            position: 0,
            state: TypingState::Countdown,
            message: String::new(),
            error: false,
            countdown: 0,
            paused_by_key: None,
        }
    }

    /// Whether the typing shortcut with `clipboard` continues this job.
    pub fn continues_with(&self, clipboard: &str) -> bool {
        self.state == TypingState::Paused && self.text == clipboard
    }

    pub fn end(&mut self, position: usize, end: RunEnd) {
        self.position = position.min(self.chars.len());
        self.countdown = 0;
        self.paused_by_key = None;
        (self.state, self.message, self.error) = match end {
            RunEnd::Completed => (TypingState::Done, "Печать завершена".into(), false),
            RunEnd::Stopped => (TypingState::Stopped, "Печать остановлена".into(), false),
            RunEnd::Interrupted { .. } if self.position >= self.chars.len() => {
                (TypingState::Done, "Печать завершена".into(), false)
            }
            RunEnd::Interrupted { message, error } => (TypingState::Paused, message, error),
        };
    }

    /// History status of a job that ends here.
    pub fn stored_status(&self) -> &'static str {
        match self.state {
            TypingState::Done => "completed",
            _ => "cancelled",
        }
    }

    pub fn view(&self, hotkey: &str) -> TypingView {
        let lines = self.chars.iter().filter(|&&c| c == '\n').count() + 1;
        let line = self.chars[..self.position].iter().filter(|&&c| c == '\n').count() + 1;
        TypingView {
            id: self.id,
            state: self.state,
            done: self.position,
            total: self.chars.len(),
            line: line.min(lines),
            lines,
            message: self.message.clone(),
            error: self.error,
            countdown: self.countdown,
            hotkey: hotkey.to_string(),
        }
    }
}

/// Text recognized in one more region of the capture in the result window.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Appended {
    pub capture: u64,
    pub text: String,
    /// The capture now shows every region, one above the other.
    pub image: String,
}

/// `addition` placed on a new line after `text`.
pub fn append_text(text: &str, addition: &str) -> String {
    let addition = addition.trim_matches('\n');
    if text.is_empty() || addition.is_empty() {
        return format!("{text}{addition}");
    }
    let separator = if text.ends_with('\n') { "" } else { "\n" };
    format!("{text}{separator}{addition}")
}

pub fn typing_settings(settings: &Settings) -> TypingSettings {
    let delay = |seconds: f64| Duration::try_from_secs_f64(seconds).unwrap_or_default().min(Duration::from_secs(2));
    TypingSettings {
        character_delay: delay(settings.character_delay),
        newline_delay: delay(settings.newline_delay),
        autoindent: settings.autoindent,
        spaces_per_tab: settings.spaces_per_tab.clamp(0, 8) as u8,
        method: if settings.typing_method == "layout" { TypingMethod::Layout } else { TypingMethod::Unicode },
        layout_delay: delay(settings.layout_delay),
    }
}

pub fn copied_message(text: &str, elapsed: Duration) -> String {
    format!("Скопировано: {} симв. за {:.1} с", text.chars().count(), elapsed.as_secs_f64())
}

/// A monitor's origin in the capture library's coordinates: physical pixels on
/// Windows, points on macOS. `physical` is the webview runtime's physical
/// origin and `scale` the monitor's scale factor.
pub fn desktop_origin(physical: (i32, i32), scale: f64, points: bool) -> (i32, i32) {
    if points && scale.is_finite() && scale > 0.0 {
        ((physical.0 as f64 / scale).round() as i32, (physical.1 as f64 / scale).round() as i32)
    } else {
        physical
    }
}

/// The capture monitor whose origin matches `origin` (rounding may differ by a
/// pixel). With a single monitor on both sides (`desktop` counts the webview
/// runtime's monitors) it is that one, wherever each side places it.
pub fn match_monitor(origin: (i32, i32), monitors: &[(u32, i32, i32)], desktop: usize) -> Option<u32> {
    monitors
        .iter()
        .filter(|(_, x, y)| (x - origin.0).abs() <= 2 && (y - origin.1).abs() <= 2)
        .min_by_key(|(_, x, y)| (x - origin.0).abs() + (y - origin.1).abs())
        .or(if monitors.len() == 1 && desktop == 1 { monitors.first() } else { None })
        .map(|(id, _, _)| *id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_monitors_by_origin() {
        let monitors = [(7, 0, 0), (9, 1728, -200)];
        // macOS: a Retina monitor at 3456x0 physical pixels is at 1728 points.
        assert_eq!(match_monitor(desktop_origin((3456, -400), 2.0, true), &monitors, 2), Some(9));
        assert_eq!(match_monitor(desktop_origin((0, 0), 2.0, true), &monitors, 2), Some(7));
        // Windows keeps physical pixels.
        assert_eq!(match_monitor(desktop_origin((1729, -199), 1.5, false), &monitors, 2), Some(9));
        assert_eq!(match_monitor((500, 500), &monitors, 2), None);
        assert_eq!(match_monitor((500, 500), &[(3, 0, 0)], 1), Some(3));
        // A second desktop monitor the capture library does not list gets no capture monitor.
        assert_eq!(match_monitor((1920, 0), &[(3, 0, 0)], 2), None);
        assert_eq!(match_monitor((0, 0), &[(3, 0, 0)], 2), Some(3));
    }

    #[test]
    fn converts_typing_settings() {
        let settings =
            Settings { character_delay: 0.008, newline_delay: 0.06, spaces_per_tab: 4, ..Settings::default() };
        let typing = typing_settings(&settings);
        assert_eq!(typing.character_delay, Duration::from_millis(8));
        assert_eq!(typing.newline_delay, Duration::from_millis(60));
        assert_eq!(typing.spaces_per_tab, 4);
        assert_eq!(typing.method, TypingMethod::Unicode);
        let layout = Settings { typing_method: "layout".into(), layout_delay: 0.3, ..Settings::default() };
        let typing = typing_settings(&layout);
        assert_eq!(typing.method, TypingMethod::Layout);
        assert_eq!(typing.layout_delay, Duration::from_millis(300));
        let broken =
            Settings { character_delay: f64::NAN, newline_delay: -1.0, spaces_per_tab: 99, ..Settings::default() };
        let typing = typing_settings(&broken);
        assert_eq!(typing.character_delay, Duration::ZERO);
        assert_eq!(typing.newline_delay, Duration::ZERO);
        assert_eq!(typing.spaces_per_tab, 8);
    }

    #[test]
    fn typing_job_keeps_its_place_across_pauses() {
        let settings = TypingSettings { spaces_per_tab: 2, ..TypingSettings::default() };
        let mut job = TypingJob::new(1, "a\tb\r\ncd\n".into(), settings);
        assert_eq!(job.chars.len(), 8);
        let view = job.view("Ctrl+Alt+V");
        assert_eq!((view.line, view.lines, view.total), (1, 3, 8));
        job.end(5, RunEnd::Interrupted { message: "focus".into(), error: false });
        assert_eq!(job.state, TypingState::Paused);
        assert!(job.continues_with("a\tb\r\ncd\n"));
        assert!(!job.continues_with("other"));
        let view = job.view("Ctrl+Alt+V");
        assert_eq!((view.done, view.line, view.message.as_str()), (5, 2, "focus"));
        // An interruption after the last character is a finished job.
        job.end(8, RunEnd::Interrupted { message: "focus".into(), error: false });
        assert_eq!((job.state, job.stored_status()), (TypingState::Done, "completed"));
        assert_eq!(job.view("").line, 3);
        job.end(3, RunEnd::Stopped);
        assert_eq!((job.state, job.stored_status()), (TypingState::Stopped, "cancelled"));
        assert!(!job.continues_with("a\tb\r\ncd\n"));
        let value = serde_json::to_value(job.view("K")).unwrap();
        assert_eq!((value["state"].clone(), value["hotkey"].clone()), ("stopped".into(), "K".into()));
    }

    #[test]
    fn appends_on_a_new_line() {
        assert_eq!(append_text("a", "b"), "a\nb");
        assert_eq!(append_text("a\n", "\nb\n"), "a\nb");
        assert_eq!(append_text("", "b"), "b");
        assert_eq!(append_text("a", ""), "a");
    }

    #[test]
    fn runs_cloud_models_together_and_local_ones_in_turn() {
        let choice = |provider: &str, model: &str| ModelChoice { provider: provider.into(), model: model.into() };
        let (cloud, local) = split_choices(&[
            choice("local", "qwen3-vl-4b"),
            choice("openrouter", "a/b"),
            choice("local", "lightonocr-3-0.8b"),
            choice("openrouter", "a/b"),
            choice("openrouter", "c/d"),
        ]);
        assert_eq!(cloud, [choice("openrouter", "a/b"), choice("openrouter", "c/d")]);
        assert_eq!(local, [choice("local", "qwen3-vl-4b"), choice("local", "lightonocr-3-0.8b")]);
        let parsed: ModelChoice = serde_json::from_str(r#"{"provider": "local", "model": "x"}"#).unwrap();
        assert!(parsed.local());
    }

    #[test]
    fn serializes_for_the_web_ui() {
        let status = serde_json::to_value(Status::error(Phase::Recognizing, "x")).unwrap();
        assert_eq!(status, serde_json::json!({"phase": "recognizing", "message": "x", "error": true}));
        let run = RunView {
            id: 1,
            capture: 1,
            round: 1,
            kind: RunKind::Review,
            provider: "local".into(),
            model: "m".into(),
            label: "M".into(),
            state: RunState::Done,
            text: "t".into(),
            base: None,
            error: None,
            elapsed_ms: 5,
        };
        let value = serde_json::to_value(run).unwrap();
        assert_eq!((value["elapsedMs"].clone(), value["kind"].clone()), (5.into(), "review".into()));
        assert_eq!(value["state"], "done");
        assert_eq!(copied_message("абв", Duration::from_millis(1300)), "Скопировано: 3 симв. за 1.3 с");
    }
}
