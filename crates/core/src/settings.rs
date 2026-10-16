//! Profile settings in the `settings.json` format the Python app reads and writes.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::catalog::PromptMode;
use crate::error::{Error, Result};
use crate::hotkeys::Hotkey;
use crate::models::ModelRegistry;

const MAX_SIZE: usize = 65536;

/// `%LOCALAPPDATA%\ctrlCctrlV` on Windows, `~/Library/Application Support/ctrlCctrlV` on macOS.
pub fn data_directory() -> PathBuf {
    let home = std::env::home_dir().unwrap_or_default();
    if cfg!(target_os = "macos") {
        return home.join("Library/Application Support/ctrlCctrlV");
    }
    // Same fallback as the Python app when LOCALAPPDATA is missing.
    std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home.join(".local/share")).join("ctrlCctrlV")
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Settings {
    pub provider: String,
    pub review_provider: String,
    pub capture_format: String,
    pub acceleration: String,
    pub local_model: String,
    pub remote_model: String,
    pub remote_mode: String,
    pub show_preview: bool,
    pub review_local_model: String,
    pub review_remote_model: String,
    pub capture_hotkey: String,
    pub typing_hotkey: String,
    pub cancel_hotkey: String,
    pub character_delay: f64,
    pub newline_delay: f64,
    pub autoindent: bool,
    pub spaces_per_tab: i64,
    pub idle_minutes: i64,
    pub max_tokens: i64,
    /// Keys this version does not know, kept so that saving does not erase
    /// settings written by a newer version.
    #[serde(flatten, skip_serializing_if = "Map::is_empty")]
    pub extra: Map<String, Value>,
}

impl Default for Settings {
    fn default() -> Self {
        let (capture, typing, cancel) = if cfg!(target_os = "windows") {
            ("ctrl+alt+4", "ctrl+alt+v", "ctrl+alt+esc")
        } else {
            ("ctrl+alt+cmd+4", "ctrl+alt+cmd+v", "ctrl+alt+cmd+esc")
        };
        Self {
            provider: "local".into(),
            review_provider: "local".into(),
            capture_format: "auto".into(),
            acceleration: "gpu".into(),
            local_model: "lightonocr-3-0.8b".into(),
            remote_model: "qwen/qwen3-vl-8b-instruct".into(),
            remote_mode: "json".into(),
            show_preview: true,
            review_local_model: "qwen3-vl-4b".into(),
            review_remote_model: "qwen/qwen3-vl-8b-instruct".into(),
            capture_hotkey: capture.into(),
            typing_hotkey: typing.into(),
            cancel_hotkey: cancel.into(),
            character_delay: 0.008,
            newline_delay: 0.060,
            autoindent: false,
            spaces_per_tab: 0,
            idle_minutes: 15,
            max_tokens: 4096,
            extra: Map::new(),
        }
    }
}

impl Settings {
    /// Reads `settings.json`; anything unreadable or invalid yields defaults and
    /// leaves the file untouched.
    pub fn load(directory: &Path, models: &ModelRegistry) -> Self {
        Self::read(directory, models).unwrap_or_default()
    }

    fn read(directory: &Path, models: &ModelRegistry) -> Option<Self> {
        use std::io::Read;
        let mut data = Vec::new();
        std::fs::File::open(directory.join("settings.json"))
            .ok()?
            .take(MAX_SIZE as u64 + 1)
            .read_to_end(&mut data)
            .ok()?;
        if data.len() > MAX_SIZE {
            return None;
        }
        let mut values: Map<String, Value> = serde_json::from_slice(&data).ok()?;
        if !values.contains_key("review_provider") {
            let provider = values.get("provider").cloned().unwrap_or_else(|| "local".into());
            values.insert("review_provider".into(), provider);
        }
        let mut settings = Self::default();
        for (name, value) in values {
            settings.assign(name, value)?;
        }
        settings.validate(models).ok()?;
        Some(settings)
    }

    /// Applies one stored value with the Python app's strict typing: booleans are
    /// never numbers, integer fields reject `1.0`, delays accept integers.
    fn assign(&mut self, name: String, value: Value) -> Option<()> {
        let text = |value: Value| match value {
            Value::String(text) => Some(text),
            _ => None,
        };
        let integer = |value: &Value| value.as_i64().filter(|_| value.is_i64() || value.is_u64());
        match name.as_str() {
            "provider" => self.provider = text(value)?,
            "review_provider" => self.review_provider = text(value)?,
            "capture_format" => self.capture_format = text(value)?,
            "acceleration" => self.acceleration = text(value)?,
            "local_model" => self.local_model = text(value)?,
            "remote_model" => self.remote_model = text(value)?,
            "remote_mode" => self.remote_mode = text(value)?,
            "review_local_model" => self.review_local_model = text(value)?,
            "review_remote_model" => self.review_remote_model = text(value)?,
            "capture_hotkey" => self.capture_hotkey = text(value)?,
            "typing_hotkey" => self.typing_hotkey = text(value)?,
            "cancel_hotkey" => self.cancel_hotkey = text(value)?,
            "show_preview" => self.show_preview = value.as_bool()?,
            "autoindent" => self.autoindent = value.as_bool()?,
            "character_delay" => self.character_delay = value.as_f64()?,
            "newline_delay" => self.newline_delay = value.as_f64()?,
            "spaces_per_tab" => self.spaces_per_tab = integer(&value)?,
            "idle_minutes" => self.idle_minutes = integer(&value)?,
            "max_tokens" => self.max_tokens = integer(&value)?,
            _ => {
                self.extra.insert(name, value);
            }
        }
        Some(())
    }

    pub fn validate(&self, models: &ModelRegistry) -> Result<()> {
        let fail = |message: &str| Err(Error::invalid(message));
        if !models.contains(&self.local_model) {
            return fail("Неизвестная локальная модель OCR");
        }
        if self.remote_model.trim().is_empty() {
            return fail("Укажите облачную модель OCR");
        }
        if models.get(&self.review_local_model).is_none_or(|model| model.mode != PromptMode::Json) {
            return fail("Для проверки выберите vision-модель с текстовыми инструкциями");
        }
        if self.review_remote_model.trim().is_empty() {
            return fail("Укажите облачную модель проверки");
        }
        let capture = Hotkey::parse(&self.capture_hotkey)?;
        let typing = Hotkey::parse(&self.typing_hotkey)?;
        let cancel = Hotkey::parse(&self.cancel_hotkey)?;
        if capture == typing || capture == cancel || typing == cancel {
            return fail("Горячие клавиши должны различаться");
        }
        if !matches!(self.provider.as_str(), "local" | "openrouter") {
            return fail("Неизвестный OCR-провайдер");
        }
        if !matches!(self.review_provider.as_str(), "local" | "openrouter") {
            return fail("Неизвестный провайдер проверки");
        }
        if !matches!(self.capture_format.as_str(), "auto" | "text" | "csv") {
            return fail("Неизвестный формат распознавания");
        }
        if !matches!(self.acceleration.as_str(), "gpu" | "cpu") {
            return fail("Выберите GPU или CPU для локальной модели");
        }
        if !matches!(self.remote_mode.as_str(), "json" | "transcription") {
            return fail("Неизвестный протокол модели");
        }
        if !(0.001..=0.2).contains(&self.character_delay) || !(0.0..=2.0).contains(&self.newline_delay) {
            return fail("Некорректная задержка ввода");
        }
        if !(0..=8).contains(&self.spaces_per_tab) || !(1..=120).contains(&self.idle_minutes) {
            return fail("Некорректный профиль ввода или время простоя");
        }
        if !(128..=8192).contains(&self.max_tokens) {
            return fail("Некорректный предел ответа");
        }
        Ok(())
    }

    /// Validates, then atomically replaces `settings.json`.
    pub fn save(&self, directory: &Path, models: &ModelRegistry) -> Result<()> {
        self.validate(models)?;
        let encoded = self.to_json();
        if encoded.len() > MAX_SIZE {
            return Err(Error::invalid("Настройки слишком большие"));
        }
        let io = |_| Error::operation("Не удалось сохранить настройки");
        std::fs::create_dir_all(directory).map_err(io)?;
        let temporary = directory.join("settings.json.tmp");
        std::fs::write(&temporary, &encoded).map_err(io)?;
        std::fs::rename(&temporary, directory.join("settings.json")).map_err(io)
    }

    /// Two-space indented UTF-8 JSON, like Python's `json.dumps(indent=2, ensure_ascii=False)`.
    pub fn to_json(&self) -> Vec<u8> {
        serde_json::to_vec_pretty(self).expect("settings serialize")
    }

    pub fn acceleration(&self) -> crate::catalog::Acceleration {
        if self.acceleration == "cpu" { crate::catalog::Acceleration::Cpu } else { crate::catalog::Acceleration::Gpu }
    }

    pub fn remote_mode(&self) -> PromptMode {
        if self.remote_mode == "transcription" { PromptMode::Transcription } else { PromptMode::Json }
    }
}
