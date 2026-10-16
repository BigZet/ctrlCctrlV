//! Pinned public artifacts; hashes are publisher SHA256 values, not inferred file sizes.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub filename: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
}

impl Artifact {
    fn pinned(filename: &str, url: &str, sha256: &str, size: u64) -> Self {
        Self { filename: filename.into(), url: url.into(), sha256: sha256.into(), size }
    }
}

/// How a model is asked to answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PromptMode {
    /// Instruction prompt; the answer is a `{"text": ...}` JSON object.
    Json,
    /// Image only; the answer is the literal transcription.
    Transcription,
}

impl PromptMode {
    pub fn as_str(self) -> &'static str {
        match self {
            PromptMode::Json => "json",
            PromptMode::Transcription => "transcription",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    pub id: String,
    pub label: String,
    pub files: Vec<Artifact>,
    pub source: String,
    #[serde(default = "default_license")]
    pub license: String,
    #[serde(default = "default_mode")]
    pub mode: PromptMode,
}

fn default_license() -> String {
    "Apache-2.0".into()
}

fn default_mode() -> PromptMode {
    PromptMode::Json
}

impl Model {
    pub fn weights(&self) -> &Artifact {
        &self.files[0]
    }

    pub fn projector(&self) -> &Artifact {
        &self.files[1]
    }

    /// Download size of all files.
    pub fn size(&self) -> u64 {
        self.files.iter().map(|file| file.size).sum()
    }
}

fn qwen(identifier: &str, parameters: u32, revision: &str, weights: (&str, u64), projector: (&str, u64)) -> Model {
    let repo = format!("unsloth/Qwen3-VL-{parameters}B-Instruct-GGUF");
    let base = format!("https://huggingface.co/{repo}/resolve/{revision}/");
    let name = format!("Qwen3-VL-{parameters}B-Instruct-Q4_K_M.gguf");
    Model {
        id: identifier.into(),
        label: format!("Qwen3-VL {parameters}B · Q4_K_M"),
        files: vec![
            Artifact::pinned(&name, &(base.clone() + &name), weights.0, weights.1),
            Artifact::pinned("mmproj-F16.gguf", &(base + "mmproj-F16.gguf"), projector.0, projector.1),
        ],
        source: format!("https://huggingface.co/{repo}/tree/{revision}"),
        license: default_license(),
        mode: PromptMode::Json,
    }
}

fn lighton(identifier: &str, parameters: &str, revision: &str, weights: (&str, u64), projector: (&str, u64)) -> Model {
    let repo = format!("prithivMLmods/LightOnOCR-3-{parameters}-GGUF");
    let base = format!("https://huggingface.co/{repo}/resolve/{revision}/");
    let weights_name = format!("LightOnOCR-3-{parameters}.Q8_0.gguf");
    let projector_name = format!("LightOnOCR-3-{parameters}.mmproj-bf16.gguf");
    Model {
        id: identifier.into(),
        label: format!("LightOnOCR-3 {parameters} · Q8 · экспериментальный"),
        files: vec![
            Artifact::pinned(&weights_name, &(base.clone() + &weights_name), weights.0, weights.1),
            Artifact::pinned(&projector_name, &(base + &projector_name), projector.0, projector.1),
        ],
        source: format!("https://huggingface.co/{repo}"),
        license: default_license(),
        mode: PromptMode::Transcription,
    }
}

/// Built-in models, keyed by the identifiers stored in settings.
pub static MODELS: LazyLock<BTreeMap<String, Model>> = LazyLock::new(|| {
    [
        lighton(
            "lightonocr-3-0.8b",
            "0.8B",
            "414757bdb78fd07c947a1e83c2b8d7e9a8b73583",
            ("660a8931ba33c8a604076f69b8785b36eadcdb1cc90004e9f89bf67615a0456d", 811_843_680),
            ("23f0d5dba9b7ef73f760e6870826e72d265a8938fdb975ed3b751853f496ac06", 207_346_496),
        ),
        lighton(
            "lightonocr-3-4b",
            "4B",
            "557c2b008f7abff778985246b1a045c635f9abdf",
            ("eb9f84e81a27f1449bd1fc91eb9b9f7eeeec49bd03483af0423839bc1bf5ffd4", 4_482_403_264),
            ("4c4f14c8bda8cd67cf4822f99c7c57b6353fd4907cba38d5535987d0db06dd8e", 675_569_216),
        ),
        qwen(
            "qwen3-vl-4b",
            4,
            "00c00da0690c4b14b5539b02c4ea5d7c9102b35e",
            ("d4dcd426bfba75752a312b266b80fec8136fbaca13c62d93b7ac41fa67f0492b", 2_497_282_336),
            ("1b9f4e92f0fbda14d7d7b58baed86039b8a980fe503d9d6a9393f25c0028f1fc", 836_180_640),
        ),
        qwen(
            "qwen3-vl-8b",
            8,
            "b93a7ee713758252c555be4210c00540df954dc2",
            ("108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89", 5_027_785_568),
            ("d406d03ebabefdef86a2c86bf0c1b65f9e046f7a81c218f25de4931b46a07fc4", 1_159_030_336),
        ),
    ]
    .into_iter()
    .map(|model| (model.id.clone(), model))
    .collect()
});

pub const ENGINE_VERSION: &str = "b11552";

const RELEASE: &str = "https://github.com/ggml-org/llama.cpp/releases/download/b11552/";

fn release(filename: &str, sha256: &str, size: u64) -> Artifact {
    Artifact::pinned(filename, &format!("{RELEASE}{filename}"), sha256, size)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Acceleration {
    #[default]
    Gpu,
    Cpu,
}

impl Acceleration {
    pub fn as_str(self) -> &'static str {
        match self {
            Acceleration::Gpu => "gpu",
            Acceleration::Cpu => "cpu",
        }
    }
}

/// Supported desktop targets. Linux is intentionally not a target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    MacArm64,
    WindowsX64,
}

impl Platform {
    /// The platform this binary was built for, if it is a supported target.
    pub fn current() -> Option<Platform> {
        if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            Some(Platform::MacArm64)
        } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
            Some(Platform::WindowsX64)
        } else {
            None
        }
    }

    /// Accepts Python `platform.system()` / `platform.machine()` spellings.
    pub fn from_names(system: &str, machine: &str) -> Option<Platform> {
        let machine = machine.to_lowercase();
        match system {
            "Darwin" if matches!(machine.as_str(), "arm64" | "aarch64") => Some(Platform::MacArm64),
            "Windows" if matches!(machine.as_str(), "amd64" | "x86_64") => Some(Platform::WindowsX64),
            _ => None,
        }
    }
}

/// llama.cpp packages for a platform; Windows GPU also needs the CUDA runtime.
pub fn engine_artifacts(platform: Option<Platform>, acceleration: Acceleration) -> Result<Vec<Artifact>> {
    match platform {
        Some(Platform::MacArm64) => Ok(vec![release(
            "llama-b11552-bin-macos-arm64.tar.gz",
            "f4b33fe40d098033fb1a19a8a4a84383c4ca84c37b6a60d9f9a253f349b48c07",
            12_086_252,
        )]),
        Some(Platform::WindowsX64) if acceleration == Acceleration::Cpu => Ok(vec![release(
            "llama-b11552-bin-win-cpu-x64.zip",
            "c00741179811ff903b515eae28bdeef1f47af6e81c49920c4e68b62954e056d6",
            19_528_406,
        )]),
        Some(Platform::WindowsX64) => Ok(vec![
            release(
                "llama-b11552-bin-win-cuda-12.4-x64.zip",
                "e48331718aa82aa34571cfad8510e6d6b9a8e4dad11a73e065f94f212e280256",
                265_234_947,
            ),
            release(
                "cudart-llama-bin-win-cuda-12.4-x64.zip",
                "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
                391_443_627,
            ),
        ]),
        None => Err(Error::operation("Локальный движок поддерживает macOS ARM64 и Windows x64")),
    }
}
