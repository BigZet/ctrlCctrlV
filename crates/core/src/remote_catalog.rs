//! The public OpenRouter catalog of vision models. Listing never requests
//! inference. The cache file `openrouter-models.json` is shared with the
//! Python app.

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};
use crate::http::Http;

const URL: &str = "https://openrouter.ai/api/v1/models?input_modalities=image&output_modalities=text";
const MAX_SIZE: usize = 8_000_000;
const CACHE: &str = "openrouter-models.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VisionModel {
    pub id: String,
    pub name: String,
    /// Dollars per million tokens, or `?`.
    pub input_price: String,
    pub output_price: String,
    /// Accepts `response_format`.
    pub structured: bool,
    #[serde(default)]
    pub temperature: bool,
    #[serde(default)]
    pub max_completion_tokens: Option<u64>,
    #[serde(default)]
    pub default_temperature: Option<f64>,
}

/// Price per token, as OpenRouter sends it, to dollars per million tokens.
pub fn price(value: Option<&Value>) -> String {
    let number = match value {
        Some(Value::String(text)) => text.trim().parse::<f64>().ok(),
        Some(Value::Number(number)) => number.as_f64(),
        _ => None,
    };
    match number.map(|n| n * 1_000_000.0) {
        Some(n) if n.is_finite() && n >= 0.0 => {
            let text = format!("{n:.4}");
            text.trim_end_matches('0').trim_end_matches('.').to_string()
        }
        _ => "?".into(),
    }
}

fn sort_price(price: &str) -> f64 {
    price.parse().unwrap_or(f64::INFINITY)
}

/// Vision models with text output, cheapest first.
pub fn parse_models(payload: &Value) -> Vec<VisionModel> {
    let mut models = std::collections::BTreeMap::new();
    for item in payload.get("data").and_then(Value::as_array).into_iter().flatten() {
        let has = |field: &str, wanted: &str| {
            item.pointer(&format!("/architecture/{field}"))
                .and_then(Value::as_array)
                .is_some_and(|values| values.iter().any(|value| value == wanted))
        };
        if !has("input_modalities", "image") || !has("output_modalities", "text") {
            continue;
        }
        let (Some(id), Some(name)) = (item.get("id").and_then(Value::as_str), item.get("name").and_then(Value::as_str))
        else {
            continue;
        };
        if id.is_empty() || id.len() > 256 {
            continue;
        }
        let supported = |parameter: &str| {
            item.get("supported_parameters")
                .and_then(Value::as_array)
                .is_some_and(|values| values.iter().any(|value| value == parameter))
        };
        let default_temperature =
            item.pointer("/default_parameters/temperature").and_then(Value::as_f64).filter(|t| (0.0..=2.0).contains(t));
        models.insert(
            id.to_string(),
            VisionModel {
                id: id.into(),
                name: name.chars().take(200).collect(),
                input_price: price(item.pointer("/pricing/prompt")),
                output_price: price(item.pointer("/pricing/completion")),
                structured: supported("response_format"),
                temperature: supported("temperature"),
                max_completion_tokens: item
                    .pointer("/top_provider/max_completion_tokens")
                    .and_then(Value::as_u64)
                    .filter(|&n| n > 0),
                default_temperature,
            },
        );
    }
    let mut models: Vec<VisionModel> = models.into_values().collect();
    models.sort_by(|a, b| sort_price(&a.input_price).total_cmp(&sort_price(&b.input_price)).then(a.name.cmp(&b.name)));
    models
}

pub async fn fetch_models(http: &Http) -> Result<Vec<VisionModel>> {
    use futures_util::StreamExt;
    let failed = || Error::operation("Не удалось загрузить каталог OpenRouter");
    let response =
        http.remote.get(URL).timeout(std::time::Duration::from_secs(20)).send().await.map_err(|_| failed())?;
    if !response.status().is_success() {
        return Err(failed());
    }
    let mut body = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        body.extend_from_slice(&chunk.map_err(|_| failed())?);
        if body.len() > MAX_SIZE {
            return Err(Error::operation("Каталог моделей слишком большой"));
        }
    }
    let payload: Value = serde_json::from_slice(&body).map_err(|_| failed())?;
    let models = parse_models(&payload);
    if models.is_empty() {
        return Err(Error::operation("В каталоге нет моделей с изображениями и текстовым ответом"));
    }
    Ok(models)
}

pub fn save_cache(directory: &Path, models: &[VisionModel]) -> Result<()> {
    let encoded = serde_json::to_vec(models).map_err(|e| Error::operation(e.to_string()))?;
    crate::fsutil::write_replace(&directory.join(CACHE), &encoded, "tmp")
        .map_err(|_| Error::operation("Не удалось сохранить каталог OpenRouter"))
}

pub fn load_cache(directory: &Path) -> Vec<VisionModel> {
    let path = directory.join(CACHE);
    if std::fs::metadata(&path).map_or(true, |m| m.len() > MAX_SIZE as u64) {
        return Vec::new();
    }
    std::fs::read(&path).ok().and_then(|data| serde_json::from_slice(&data).ok()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_vision_models_cheapest_first() {
        let payload = serde_json::json!({"data": [
            {"id": "b/vision", "name": "B", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0.0000002", "completion": "0.000001"}, "supported_parameters": ["response_format", "temperature"],
             "top_provider": {"max_completion_tokens": 8192}, "default_parameters": {"temperature": 0.7}},
            {"id": "a/free", "name": "A", "architecture": {"input_modalities": ["image"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "c/text", "name": "C", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
            {"id": "d/odd", "name": "D", "architecture": {"input_modalities": ["image"], "output_modalities": ["text"]},
             "pricing": {"prompt": "-1"}},
        ]});
        let models = parse_models(&payload);
        let ids: Vec<&str> = models.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, ["a/free", "b/vision", "d/odd"]);
        assert_eq!((models[1].input_price.as_str(), models[1].output_price.as_str()), ("0.2", "1"));
        assert!(models[1].structured && models[1].temperature && !models[0].structured);
        assert_eq!(models[1].max_completion_tokens, Some(8192));
        assert_eq!(models[1].default_temperature, Some(0.7));
        assert_eq!(models[2].input_price, "?");
    }

    #[test]
    fn cache_round_trips_and_reads_the_python_format() {
        let directory = tempfile::tempdir().unwrap();
        assert!(load_cache(directory.path()).is_empty());
        std::fs::write(
            directory.path().join(CACHE),
            r#"[{"id": "q/x", "name": "Q", "input_price": "0.1", "output_price": "?", "structured": true, "temperature": false, "max_completion_tokens": null, "default_temperature": null}]"#,
        )
        .unwrap();
        let models = load_cache(directory.path());
        assert_eq!(models[0].id, "q/x");
        save_cache(directory.path(), &models).unwrap();
        assert_eq!(load_cache(directory.path()), models);
    }
}
