//! Built-in models plus per-profile GGUF bundles pinned to publisher hashes.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;

use crate::catalog::{MODELS, Model};
use crate::error::{Error, Result};
use crate::fsutil::write_replace;

static CUSTOM_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\Acustom-[a-f0-9]{16}\z").unwrap());
static SHA256: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\A[a-f0-9]{64}\z").unwrap());

/// Rejects anything but a pinned, public two-file vision bundle.
pub fn validate_custom(model: &Model) -> Result<()> {
    if !CUSTOM_ID.is_match(&model.id) {
        return Err(Error::invalid("Некорректная пользовательская модель"));
    }
    let names: Vec<&str> = model.files.iter().map(|file| file.filename.as_str()).collect();
    if names != ["model.gguf", "mmproj.gguf"] {
        return Err(Error::invalid("Для vision-модели нужны GGUF и mmproj"));
    }
    for file in &model.files {
        let public = reqwest::Url::parse(&file.url).is_ok_and(|url| {
            url.scheme() == "https"
                && url.host_str().is_some_and(|host| !host.is_empty())
                && url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none()
        });
        if !public {
            return Err(Error::invalid("Нужны публичные HTTPS-ссылки без паролей и параметров"));
        }
        if !SHA256.is_match(&file.sha256) || !(1..200_000_000_000).contains(&file.size) {
            return Err(Error::invalid("Не получены SHA256 и размер GGUF"));
        }
    }
    Ok(())
}

/// All models available to this profile: the built-in catalog plus
/// `custom-models.json`, in the format the Python app writes.
#[derive(Debug, Clone)]
pub struct ModelRegistry {
    path: PathBuf,
    models: BTreeMap<String, Model>,
    errors: Vec<String>,
}

impl ModelRegistry {
    /// Only the built-in catalog, without reading a profile.
    pub fn builtin() -> Self {
        Self { path: PathBuf::new(), models: MODELS.clone(), errors: Vec::new() }
    }

    pub fn load(directory: &Path) -> Self {
        let mut registry = Self { path: directory.join("custom-models.json"), ..Self::builtin() };
        if !registry.path.exists() {
            return registry;
        }
        match read_custom(&registry.path) {
            Ok(models) => {
                for model in models {
                    registry.models.insert(model.id.clone(), model);
                }
            }
            Err(_) => registry.errors.push("Не удалось прочитать каталог добавленных моделей".into()),
        }
        registry
    }

    pub fn get(&self, id: &str) -> Option<&Model> {
        self.models.get(id)
    }

    pub fn contains(&self, id: &str) -> bool {
        self.models.contains_key(id)
    }

    pub fn iter(&self) -> impl Iterator<Item = &Model> {
        self.models.values()
    }

    /// Problems found while loading; the built-in models stay usable.
    pub fn errors(&self) -> &[String] {
        &self.errors
    }

    pub fn add(&mut self, model: Model) -> Result<()> {
        validate_custom(&model)?;
        let mut custom: Vec<&Model> = self.models.values().filter(|m| !MODELS.contains_key(&m.id)).collect();
        custom.retain(|m| m.id != model.id);
        custom.push(&model);
        let encoded = serde_json::to_vec_pretty(&custom).map_err(|e| Error::operation(e.to_string()))?;
        write_replace(&self.path, &encoded, "tmp")
            .map_err(|_| Error::operation("Не удалось сохранить каталог моделей"))?;
        self.models.insert(model.id.clone(), model);
        Ok(())
    }
}

fn read_custom(path: &Path) -> Result<Vec<Model>, Box<dyn std::error::Error>> {
    if std::fs::metadata(path)?.len() > 1_000_000 {
        return Err("Каталог слишком большой".into());
    }
    let models: Vec<Model> = serde_json::from_slice(&std::fs::read(path)?)?;
    for model in &models {
        validate_custom(model)?;
    }
    Ok(models)
}
