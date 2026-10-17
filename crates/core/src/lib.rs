//! ctrlCctrlV core without UI: settings, history, model catalog, the OCR
//! client for the local engine and OpenRouter, and `llama-server` management.
//!
//! On-disk data (settings, history, model receipts) and the stored API key
//! stay compatible with the Python app. Targets are Windows x64 and macOS ARM64.

pub mod catalog;
pub mod credentials;
pub mod download;
pub mod error;
pub mod extract;
mod fsutil;
pub mod hardware;
pub mod history;
pub mod hotkeys;
pub mod http;
pub mod keyfile;
pub mod lighton;
pub mod models;
pub mod ocr;
pub mod operation;
pub mod pyjson;
pub mod remote_catalog;
pub mod review;
pub mod runtime;
pub mod settings;
pub mod stream;
pub mod ui_state;

pub use error::{Error, Result};
