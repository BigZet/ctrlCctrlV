//! Portable shell logic: what the app is doing, the payloads sent to the web
//! UI, and how desktop monitors map to capture monitors. Kept free of Tauri so
//! the tests run on any host.
use std::time::Duration;

use ctrlcctrlv_core::settings::Settings;
use ctrlcctrlv_platform::typing::TypingSettings;
use serde::{Deserialize, Serialize};

/// Event names shared with `ui/*.js`.
pub mod events {
    /// [`super::Status`]: the app's phase and the last message.
    pub const STATUS: &str = "status";
    /// [`super::SessionView`]: a new capture opened in the result window.
    pub const SESSION: &str = "ocr-session";
    /// [`super::RunView`]: a model's answer for the capture, sent again while it streams.
    pub const RUN: &str = "ocr-run";
    /// [`super::TypingProgress`].
    pub const TYPING: &str = "typing-progress";
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct TypingProgress {
    pub done: usize,
    pub total: usize,
}

pub fn typing_settings(settings: &Settings) -> TypingSettings {
    let delay = |seconds: f64| Duration::try_from_secs_f64(seconds).unwrap_or_default().min(Duration::from_secs(2));
    TypingSettings {
        character_delay: delay(settings.character_delay),
        newline_delay: delay(settings.newline_delay),
        autoindent: settings.autoindent,
        spaces_per_tab: settings.spaces_per_tab.clamp(0, 8) as u8,
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
/// pixel). With a single monitor on both sides it is that one.
pub fn match_monitor(origin: (i32, i32), monitors: &[(u32, i32, i32)]) -> Option<u32> {
    monitors
        .iter()
        .filter(|(_, x, y)| (x - origin.0).abs() <= 2 && (y - origin.1).abs() <= 2)
        .min_by_key(|(_, x, y)| (x - origin.0).abs() + (y - origin.1).abs())
        .or(if monitors.len() == 1 { monitors.first() } else { None })
        .map(|(id, _, _)| *id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_monitors_by_origin() {
        let monitors = [(7, 0, 0), (9, 1728, -200)];
        // macOS: a Retina monitor at 3456x0 physical pixels is at 1728 points.
        assert_eq!(match_monitor(desktop_origin((3456, -400), 2.0, true), &monitors), Some(9));
        assert_eq!(match_monitor(desktop_origin((0, 0), 2.0, true), &monitors), Some(7));
        // Windows keeps physical pixels.
        assert_eq!(match_monitor(desktop_origin((1729, -199), 1.5, false), &monitors), Some(9));
        assert_eq!(match_monitor((500, 500), &monitors), None);
        assert_eq!(match_monitor((500, 500), &[(3, 0, 0)]), Some(3));
    }

    #[test]
    fn converts_typing_settings() {
        let settings =
            Settings { character_delay: 0.008, newline_delay: 0.06, spaces_per_tab: 4, ..Settings::default() };
        let typing = typing_settings(&settings);
        assert_eq!(typing.character_delay, Duration::from_millis(8));
        assert_eq!(typing.newline_delay, Duration::from_millis(60));
        assert_eq!(typing.spaces_per_tab, 4);
        let broken =
            Settings { character_delay: f64::NAN, newline_delay: -1.0, spaces_per_tab: 99, ..Settings::default() };
        let typing = typing_settings(&broken);
        assert_eq!(typing.character_delay, Duration::ZERO);
        assert_eq!(typing.newline_delay, Duration::ZERO);
        assert_eq!(typing.spaces_per_tab, 8);
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
