//! Portable shell logic: what the app is doing, the payloads sent to the web
//! UI, and how desktop monitors map to capture monitors. Kept free of Tauri so
//! the tests run on any host.
use std::time::Duration;

use ctrlcctrlv_core::settings::Settings;
use ctrlcctrlv_platform::typing::TypingSettings;
use serde::Serialize;

/// Event names shared with `ui/*.js`.
pub mod events {
    /// [`super::Status`]: the app's phase and the last message.
    pub const STATUS: &str = "status";
    /// [`super::RunView`]: one model's answer for a capture.
    pub const RESULT: &str = "ocr-result";
    /// [`super::TypingProgress`].
    pub const TYPING: &str = "typing-progress";
    /// Sent to the overlays right before they are shown, to clear the last selection.
    pub const OVERLAY_RESET: &str = "overlay-reset";
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

/// One model's answer for a capture. A capture keeps its image, so later runs
/// (another model, a re-check) arrive as more `RunView`s with the same `capture`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunView {
    pub capture: u64,
    /// `local` or `openrouter`, as stored in history.
    pub provider: String,
    pub model: String,
    pub text: String,
    pub elapsed_ms: u64,
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
    fn serializes_for_the_web_ui() {
        let status = serde_json::to_value(Status::error(Phase::Recognizing, "x")).unwrap();
        assert_eq!(status, serde_json::json!({"phase": "recognizing", "message": "x", "error": true}));
        let run = RunView { capture: 1, provider: "local".into(), model: "m".into(), text: "t".into(), elapsed_ms: 5 };
        assert_eq!(serde_json::to_value(run).unwrap()["elapsedMs"], 5);
        assert_eq!(copied_message("абв", Duration::from_millis(1300)), "Скопировано: 3 симв. за 1.3 с");
    }
}
