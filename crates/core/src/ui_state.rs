//! Presentation preferences in `ui-state.json`, shared with the Python app:
//! pinned history entries, favourite OpenRouter models and the models the
//! result window checks a capture with by default.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::error::{Error, Result};
use crate::history::EntryRef;

const MAX_SIZE: usize = 65536;
const MAX_PINS: usize = 100;
const MAX_FAVORITES: usize = 1000;
const MAX_REVIEW_MODELS: usize = 50;

/// A model by provider (`local` or `openrouter`) and identifier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelRef {
    pub provider: String,
    pub model: String,
}

#[derive(Debug, Clone)]
pub struct UiState {
    path: PathBuf,
    /// Every stored key, so saving keeps values this version does not use.
    values: Map<String, Value>,
}

impl UiState {
    pub fn load(directory: &Path) -> Self {
        let path = directory.join("ui-state.json");
        let values = std::fs::read(&path)
            .ok()
            .filter(|data| data.len() <= MAX_SIZE)
            .and_then(|data| serde_json::from_slice(&data).ok())
            .unwrap_or_default();
        Self { path, values }
    }

    /// Pins that pass the Python app's validation; anything else counts as none.
    pub fn history_pins(&self) -> Vec<EntryRef> {
        let parse = |value: &Value| {
            let object = value.as_object().filter(|o| o.len() == 2)?;
            let id = object.get("id")?.as_i64().filter(|&id| id > 0)?;
            let created = object.get("created")?.as_str().filter(|c| c.len() <= 64)?;
            Some(EntryRef { id, created: created.into() })
        };
        let Some(Value::Array(items)) = self.values.get("history_pins") else { return Vec::new() };
        let pins: Option<Vec<EntryRef>> = items.iter().map(parse).collect();
        pins.filter(|pins| pins.len() <= MAX_PINS).unwrap_or_default()
    }

    pub fn set_history_pins(&mut self, pins: &[EntryRef]) -> Result<()> {
        if pins.len() > MAX_PINS {
            return Err(Error::invalid("Можно закрепить не больше 100 записей"));
        }
        let pins: Vec<Value> = pins.iter().map(|pin| json!({"id": pin.id, "created": pin.created})).collect();
        self.set("history_pins", Value::Array(pins))
    }

    pub fn favorites(&self) -> Vec<String> {
        let Some(Value::Array(items)) = self.values.get("favorites") else { return Vec::new() };
        let favorites: Option<Vec<String>> = items
            .iter()
            .map(|item| item.as_str().filter(|s| !s.is_empty() && s.len() <= 256).map(str::to_string))
            .collect();
        favorites.filter(|f| f.len() <= MAX_FAVORITES).unwrap_or_default()
    }

    /// OpenRouter favourites by model id. The Python app stores every
    /// favourite as `source:id` (`openrouter:…`, `local:…`); a bare id is one an
    /// earlier Rust build wrote.
    pub fn remote_favorites(&self) -> Vec<String> {
        self.favorites()
            .iter()
            .filter_map(|key| match key.split_once(':') {
                Some(("openrouter", id)) => Some(id),
                Some(("local", _)) => None,
                _ => Some(key.as_str()),
            })
            .map(str::to_string)
            .collect()
    }

    /// Adds or removes an OpenRouter favourite in the Python app's format and
    /// returns the remaining OpenRouter ids.
    pub fn set_remote_favorite(&mut self, id: &str, favorite: bool) -> Result<Vec<String>> {
        let key = format!("openrouter:{id}");
        let mut favorites: Vec<String> = self.favorites().into_iter().filter(|f| *f != key && f != id).collect();
        if favorite {
            favorites.push(key);
        }
        favorites.sort();
        self.set_favorites(&favorites)?;
        Ok(self.remote_favorites())
    }

    pub fn set_favorites(&mut self, favorites: &[String]) -> Result<()> {
        if favorites.len() > MAX_FAVORITES || favorites.iter().any(|f| f.is_empty() || f.len() > 256) {
            return Err(Error::invalid("Некорректный список избранных моделей"));
        }
        self.set("favorites", json!(favorites))
    }

    /// The models checked by default in the result window, in order.
    pub fn review_models(&self) -> Vec<ModelRef> {
        let Some(Value::Array(items)) = self.values.get("review_models") else { return Vec::new() };
        let models: Option<Vec<ModelRef>> = items
            .iter()
            .map(|item| serde_json::from_value::<ModelRef>(item.clone()).ok().filter(ModelRef::plausible))
            .map(|model| model.map(ModelRef::normalized))
            .collect();
        models.filter(|m| m.len() <= MAX_REVIEW_MODELS).unwrap_or_default()
    }

    pub fn set_review_models(&mut self, models: &[ModelRef]) -> Result<()> {
        if models.len() > MAX_REVIEW_MODELS || !models.iter().all(ModelRef::plausible) {
            return Err(Error::invalid("Некорректный список моделей для проверки"));
        }
        self.set("review_models", json!(models))
    }

    /// Whether the first-run setup was completed or skipped.
    pub fn setup_done(&self) -> bool {
        self.values.get("setup_done").and_then(Value::as_bool).unwrap_or(false)
    }

    pub fn set_setup_done(&mut self) -> Result<()> {
        self.set("setup_done", json!(true))
    }

    fn set(&mut self, key: &str, value: Value) -> Result<()> {
        let mut values = self.values.clone();
        values.insert(key.into(), value);
        let encoded = serde_json::to_vec_pretty(&values).expect("ui state");
        if encoded.len() > MAX_SIZE {
            return Err(Error::invalid("Состояние интерфейса слишком большое"));
        }
        if let Some(directory) = self.path.parent() {
            let _ = std::fs::create_dir_all(directory);
        }
        crate::fsutil::write_replace(&self.path, &encoded, "json.tmp")
            .map_err(|_| Error::operation("Не удалось сохранить состояние интерфейса"))?;
        self.values = values;
        Ok(())
    }
}

impl ModelRef {
    /// A favourite key saved as a model id by an earlier build (`openrouter:x`).
    fn normalized(mut self) -> Self {
        if self.provider == "openrouter"
            && let Some(id) = self.model.strip_prefix("openrouter:")
        {
            self.model = id.to_string();
        }
        self
    }

    fn plausible(&self) -> bool {
        matches!(self.provider.as_str(), "local" | "openrouter") && !self.model.is_empty() && self.model.len() <= 256
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_unknown_keys_and_validates_pins() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("ui-state.json");
        std::fs::write(
            &path,
            r#"{"preview_sizes": [440, 560], "history_pins": [{"id": 3, "created": "c"}], "favorites": ["a/b"]}"#,
        )
        .unwrap();
        let mut state = UiState::load(directory.path());
        assert_eq!(state.history_pins(), [EntryRef { id: 3, created: "c".into() }]);
        assert_eq!(state.favorites(), ["a/b"]);
        assert_eq!(state.remote_favorites(), ["a/b"]);
        state.set_history_pins(&[EntryRef { id: 5, created: "d".into() }]).unwrap();
        let stored: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored["preview_sizes"], json!([440, 560]));
        assert_eq!(stored["history_pins"], json!([{"id": 5, "created": "d"}]));
        std::fs::write(&path, r#"{"history_pins": [{"id": -1, "created": "c"}]}"#).unwrap();
        assert!(UiState::load(directory.path()).history_pins().is_empty());
    }

    #[test]
    fn favorites_use_the_python_source_prefix() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("ui-state.json");
        std::fs::write(&path, r#"{"favorites": ["local:qwen3-vl-4b", "openrouter:q/x:free", "legacy/bare"]}"#).unwrap();
        let mut state = UiState::load(directory.path());
        assert_eq!(state.remote_favorites(), ["q/x:free", "legacy/bare"]);
        assert_eq!(state.set_remote_favorite("legacy/bare", false).unwrap(), ["q/x:free"]);
        assert_eq!(state.set_remote_favorite("a/b", true).unwrap(), ["a/b", "q/x:free"]);
        let stored: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored["favorites"], json!(["local:qwen3-vl-4b", "openrouter:a/b", "openrouter:q/x:free"]));
        std::fs::write(&path, r#"{"review_models": [{"provider": "openrouter", "model": "openrouter:a/b"}]}"#).unwrap();
        assert_eq!(UiState::load(directory.path()).review_models()[0].model, "a/b");
    }

    #[test]
    fn review_models_round_trip_and_reject_unknown_providers() {
        let directory = tempfile::tempdir().unwrap();
        let mut state = UiState::load(directory.path());
        assert!(state.review_models().is_empty());
        let models = vec![
            ModelRef { provider: "local".into(), model: "qwen3-vl-4b".into() },
            ModelRef { provider: "openrouter".into(), model: "anthropic/claude-opus-5.5".into() },
        ];
        state.set_review_models(&models).unwrap();
        assert_eq!(UiState::load(directory.path()).review_models(), models);
        assert!(state.set_review_models(&[ModelRef { provider: "cloud".into(), model: "x".into() }]).is_err());
        std::fs::write(
            directory.path().join("ui-state.json"),
            r#"{"review_models": [{"provider": "x", "model": "y"}]}"#,
        )
        .unwrap();
        assert!(UiState::load(directory.path()).review_models().is_empty());
    }
}
