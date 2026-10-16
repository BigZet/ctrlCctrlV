//! Cross-checks against the Python app's own code: files written by one
//! version are read by the other, and pure functions give identical results.
//!
//! Needs `python3` with the app's dependencies (at least `httpx`). Without
//! them the tests are skipped, unless `CTRLCCTRLV_REQUIRE_PYTHON=1` is set
//! (as in CI), which turns a missing interpreter into a failure.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use ctrlcctrlv_core::catalog::{Acceleration, MODELS, PromptMode};
use ctrlcctrlv_core::history::{History, NewRun};
use ctrlcctrlv_core::hotkeys::Hotkey;
use ctrlcctrlv_core::lighton::clean_lighton_code;
use ctrlcctrlv_core::models::ModelRegistry;
use ctrlcctrlv_core::ocr::{OcrProvider, Request, http_error};
use ctrlcctrlv_core::pyjson;
use ctrlcctrlv_core::runtime::{LocalRuntime, Session};
use ctrlcctrlv_core::settings::Settings;
use serde_json::{Value, json};

fn repository() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap()
}

fn interpreter() -> Option<String> {
    let candidates =
        std::env::var("CTRLCCTRLV_PYTHON").map(|p| vec![p]).unwrap_or_else(|_| vec!["python3".into(), "python".into()]);
    let found = candidates
        .into_iter()
        .find(|python| call_with(python, json!({"command": "available"})).is_some_and(|v| v == json!(true)));
    if found.is_none() {
        assert!(std::env::var_os("CTRLCCTRLV_REQUIRE_PYTHON").is_none(), "Python app with httpx is required");
        eprintln!("skipped: Python app with httpx not available");
    }
    found
}

fn call_with(python: &str, command: Value) -> Option<Value> {
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/python/compat.py");
    let mut child = Command::new(python)
        .arg(script)
        .env("PYTHONPATH", repository().join("src"))
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .ok()?;
    child.stdin.take().unwrap().write_all(command.to_string().as_bytes()).ok()?;
    let output = child.wait_with_output().ok()?;
    if !output.status.success() {
        eprintln!("{}", String::from_utf8_lossy(&output.stderr));
        return None;
    }
    serde_json::from_slice(&output.stdout).ok()
}

macro_rules! python {
    () => {
        match interpreter() {
            Some(python) => move |command: Value| call_with(&python, command).expect("python command failed"),
            None => return,
        }
    };
}

#[test]
fn settings_written_by_either_version_are_read_by_the_other() {
    let python = python!();
    let models = ModelRegistry::builtin();
    let directory = tempfile::tempdir().unwrap();
    let dir = directory.path().to_str().unwrap();
    let values = json!({
        "provider": "openrouter", "review_provider": "local", "remote_model": "google/gemma-3-27b-it",
        "show_preview": false, "autoindent": true, "spaces_per_tab": 4, "character_delay": 0.015,
        "newline_delay": 0.25, "max_tokens": 2048, "capture_hotkey": "ctrl+shift+cmd+5", "local_model": "qwen3-vl-8b",
        "capture_format": "csv", "acceleration": "cpu", "idle_minutes": 30, "remote_mode": "transcription",
    });
    python(json!({"command": "settings_save", "directory": dir, "values": values}));
    let loaded = Settings::load(directory.path(), &models);
    let loaded_json: Value = serde_json::from_slice(&loaded.to_json()).unwrap();
    let python_view = python(json!({"command": "settings_load", "directory": dir}));
    assert_eq!(loaded_json, python_view);
    assert_eq!(loaded.acceleration(), Acceleration::Cpu);
    assert_eq!(loaded.remote_mode(), PromptMode::Transcription);
    // Python's own file and the Rust file are byte-identical.
    let python_bytes = std::fs::read(directory.path().join("settings.json")).unwrap();
    assert_eq!(String::from_utf8(loaded.to_json()).unwrap(), String::from_utf8(python_bytes).unwrap());

    let changed =
        Settings { review_remote_model: "qwen/Ж-модель".into(), typing_hotkey: "alt+cmd+v".into(), ..loaded };
    changed.save(directory.path(), &models).unwrap();
    let python_view = python(json!({"command": "settings_load", "directory": dir}));
    assert_eq!(python_view, serde_json::from_slice::<Value>(&changed.to_json()).unwrap());
}

#[test]
fn history_is_shared_in_both_directions() {
    let python = python!();
    let directory = tempfile::tempdir().unwrap();
    let dir = directory.path().to_str().unwrap();
    let ids = python(json!({"command": "history_add", "directory": dir, "items": [
        ["ocr", "Straße_50%", "lightonocr-3-0.8b", "completed"], ["typing", "Папка\r\n", "", "completed"]]}));
    let history = History::open(directory.path()).unwrap();
    let rust: Vec<Value> = history
        .entries("", 500)
        .unwrap()
        .into_iter()
        .map(|e| json!({"id": e.id, "created": e.created, "kind": e.kind, "text": e.text, "model": e.model, "status": e.status}))
        .collect();
    assert_eq!(Value::Array(rust.clone()), python(json!({"command": "history_entries", "directory": dir})));
    assert_eq!(history.entries("STRASSE", 500).unwrap()[0].id, ids[0].as_i64().unwrap());

    let added = history.add("ocr", "From Rust ПАПКА", "qwen3-vl-4b", "completed").unwrap().unwrap();
    let entry = history.get(added).unwrap().unwrap();
    let run = NewRun {
        provider: "openrouter",
        model: "x/y",
        text: "alt",
        raw_text: "alt",
        status: "completed",
        elapsed_ms: None,
    };
    history.add_run(&entry.reference(), run).unwrap().unwrap();
    let python_entries = python(json!({"command": "history_entries", "directory": dir, "search": "папка"}));
    assert_eq!(python_entries.as_array().unwrap().len(), 2);
    assert_eq!(python_entries[0]["id"], added);
    assert_eq!(python_entries[0]["created"], entry.created);
    // The Python app still deletes entries in a database with the extra table.
    assert_eq!(python(json!({"command": "history_delete", "directory": dir, "id": added}))["text"], "From Rust ПАПКА");
    history.compact().unwrap();
    assert!(history.runs(&entry.reference()).unwrap().is_empty());
}

fn lighton_corpus() -> Vec<String> {
    let mut corpus: Vec<String> = [
        "python\nThis is a language name.\n",
        "python = 3\nprint(python)\n",
        "python\nfrom the documentation, choose the correct version.\n",
        "python\nif you need help, read the documentation.\n",
        "def f():\n    python = \"python\"\n",
        "python\n",
        "python\r\nx = 1\r\n",
        "python\\nx = 1",
        "```python\n\n    x = 1  \n\n```\n",
        "```py\nx\n```",
        "```PYTHON3 \r\nprint(1)\r\n```\r\n",
        "```python\na\n```\nb\n```\n",
        "python\n27  def main():\n28      parser = argparse.ArgumentParser()\n",
        "python\n[build-system]\nrequires = [\"setuptools>=68\"]\n",
        "python\n[build-system]\nrequires = [\"setuptools>=68\"]\n\n[project]\ninvalid OCR fragment\n",
        "python\n[tool]\n",
        "python\n[empty]\n\nkey = 1\n",
        "python\nkey = 1\n",
        "python\nimport argparse\n\ndef main():\n    pass\n",
        "python\n    x = compute()\n    y += 1\n",
        "python\nhello world\n",
        "python\nhello\n",
        "python\nhello, world\n",
        "python\n1 + 2\n",
        "python\n'string'\n",
        "python\nx\ny\n",
        "python\nfoo.bar\n",
        "python\nfoo.bar()\n",
        "python\n# comment only\n",
        "python\nif x:\n    pass\n",
        "python\nfor i in range(3): print(i)\n",
        "python\nreturn 5\n",
        "python\nprint 'py2'\n",
        "python\nlambda: 0\n",
        "python\n@decorator\ndef f(): pass\n",
        "python\nasync def f(): pass\n",
        "python\nclass A: pass\n",
        "python\nwith open(f) as g:\n    pass\n",
        "python\nmatch x:\n    case 1:\n        pass\n",
        "python\nx: int = 1\n",
        "python\ndel x\n",
        "python\nassert x\n",
        "python\nglobal x\n",
        "python\na, b\n",
        "python\n  indented\n    more\n",
        "python\n\tprint(1)\n",
        "python\nКод: значение\n",
        "python\nf\"{x}\"\n",
        "python\n(1,\n",
        "python\n[1, 2]\n",
        "python\n{'a': 1}\n",
        "python\nnot x\n",
        "python\nawait x\n",
        "python\nyield x\n",
        "python\ntry:\n    pass\nexcept:\n    pass\n",
        "python\nwhile True: break\n",
        "python\nraise ValueError()\n",
        "python\nfrom . import x\n",
        "python\nx = 1 # c\n",
        "python\n;\n",
        "python\n...\n",
        "python\nTrue\n",
        "python\n1 if x else 2\n",
        "python\nx[0] = 1\n",
        "python\nx.y = 2\n",
        "python\nnonlocal x\n",
        "python\ntype X = int\n",
        "python\nprint(\n",
        "python\n\0\n",
        "Py\nx = 1\n",
        "python3 \nx = 1\n",
        "pythonx = 1\n",
        "text without label\n",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    corpus.extend(corpus.clone().into_iter().map(|s| s.replace('\n', "\r\n")));
    corpus
}

#[test]
fn lighton_cleanup_matches_python() {
    let python = python!();
    let corpus = lighton_corpus();
    let expected = python(json!({"command": "lighton", "texts": corpus}));
    let mismatches: Vec<_> = corpus
        .iter()
        .zip(expected.as_array().unwrap())
        .filter(|(text, expected)| clean_lighton_code(text) != expected.as_str().unwrap())
        .map(|(text, expected)| format!("{text:?}: rust {:?}, python {expected}", clean_lighton_code(text)))
        .collect();
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

#[test]
fn request_payloads_match_python() {
    let python = python!();
    let context =
        json!({"text": "Было\n", "lines": [1, 2.5, -0.0001], "nested": {"ok": true, "none": null}, "big": 1e20});
    let cases = [
        (
            "https://openrouter.ai/api/v1/chat/completions",
            "qwen/qwen3-vl-8b-instruct",
            false,
            PromptMode::Json,
            None,
            None,
        ),
        ("http://127.0.0.1:1/v1/chat/completions", "lightonocr-3-0.8b", true, PromptMode::Transcription, None, None),
        (
            "http://127.0.0.1:1/v1/chat/completions",
            "qwen3-vl-4b",
            true,
            PromptMode::Json,
            Some("Сверь текст"),
            Some(&context),
        ),
        (
            "https://openrouter.ai/api/v1/chat/completions",
            "x/transcriber",
            false,
            PromptMode::Transcription,
            Some("ignored"),
            Some(&context),
        ),
    ];
    let png: Vec<u8> = (0u8..=255).collect();
    for (endpoint, model, local, mode, prompt, context) in cases {
        let mut provider = OcrProvider::new(endpoint, model, "secret").mode(mode).max_tokens(1024);
        if local {
            provider = provider.local();
        }
        let rust = provider.payload(&png, &Request { prompt, context });
        let answer = python(json!({
            "command": "payload", "endpoint": endpoint, "model": model, "key": "secret", "max_tokens": 1024,
            "local": local, "mode": mode.as_str(), "prompt": prompt, "context": context,
            "png": png.iter().map(|&b| b as char).collect::<String>(),
        }));
        assert_eq!(rust, answer["payload"], "{model}");
        assert_eq!(answer["authorization"], "Bearer secret");
    }
}

#[test]
fn error_messages_match_python() {
    let python = python!();
    let cases: Vec<(u16, &str)> = vec![
        (400, r#"{"error":{"message":"response_format unsupported"}}"#),
        (400, r#"{"error":{"message":"x","metadata":{"raw":"json_schema"}}}"#),
        (400, r#"{"error":{"message":"Temperature"}}"#),
        (400, r#"{"error":{"message":"max_completion_tokens too large"}}"#),
        (400, r#"{"error":{"message":"Context Window"}}"#),
        (400, r#"{"error":{"message":"modalities"}}"#),
        (400, r#"{"error":{"message":"other"}}"#),
        (400, r#"{"error":"plain"}"#),
        (400, r#"[]"#),
        (400, "not json"),
        (400, r#"{"error":{"metadata":{"raw":5}}}"#),
        (401, ""),
        (402, ""),
        (403, ""),
        (404, ""),
        (429, ""),
        (500, ""),
        (503, "x"),
    ];
    let expected = python(json!({"command": "http_errors", "cases": cases}));
    for ((status, body), expected) in cases.iter().zip(expected.as_array().unwrap()) {
        assert_eq!(http_error(*status, body.as_bytes()), expected.as_str().unwrap(), "{status} {body}");
    }
}

#[test]
fn hotkey_parsing_matches_python() {
    let python = python!();
    let values = [
        "ctrl+alt+4",
        "CTRL + ALT + V",
        "win+v",
        "cmd+win+v",
        "shift+4",
        "ctrl+ctrl+4",
        "ctrl+f1",
        "4",
        "",
        "meta+4",
        "ctrl+alt+cmd+esc",
        "Shift+Ctrl+Z",
        "alt+",
        "+4",
        "ctrl++4",
    ];
    let expected = python(json!({"command": "hotkeys", "values": values}));
    for (value, expected) in values.iter().zip(expected.as_array().unwrap()) {
        let rust = match Hotkey::parse(value) {
            Ok(hotkey) => {
                let mut modifiers: Vec<&str> = hotkey.modifiers.iter().map(|m| m.as_str()).collect();
                modifiers.sort();
                json!({"modifiers": modifiers, "key": hotkey.key})
            }
            Err(error) => json!({"error": error.to_string()}),
        };
        assert_eq!(&rust, expected, "{value}");
    }
}

#[test]
fn json_text_and_floats_match_python() {
    let python = python!();
    let floats =
        [0.1, 1.0, 1e16, 1.5e16, 1e-4, 1e-5, 0.00012345, 123456789.123, 2.5, -3.75, 1e300, 5e-324, 9999999999999998.0];
    let expected = python(json!({"command": "float_repr", "values": floats}));
    for (value, expected) in floats.iter().zip(expected.as_array().unwrap()) {
        assert_eq!(pyjson::float_repr(*value), expected.as_str().unwrap());
    }
    let values =
        [json!({"a": [1, 2.0, "Ж\n\t\"\\\u{1}"], "b": null, "c": {"d": false}}), json!("\u{7f}\u{2028}"), json!([])];
    let expected = python(json!({"command": "dumps", "values": values}));
    for (value, expected) in values.iter().zip(expected.as_array().unwrap()) {
        assert_eq!(pyjson::dumps(value), expected.as_str().unwrap());
    }
}

#[tokio::test]
async fn engine_and_model_receipts_are_accepted_by_both_versions() {
    let python = python!();
    let directory = tempfile::tempdir().unwrap();
    let dir = directory.path().to_str().unwrap();
    let runtime =
        LocalRuntime::new(directory.path(), Session::Ocr, 1, Acceleration::Gpu, ModelRegistry::builtin()).unwrap();
    // Build an engine package the way the release archives are laid out.
    let archive = directory.path().join("engine.tar.gz");
    {
        let file = std::fs::File::create(&archive).unwrap();
        let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(file, flate2::Compression::fast()));
        for name in ["build/bin/llama-server", "build/bin/llama-server.exe", "build/bin/libggml.dylib"] {
            let mut header = tar::Header::new_gnu();
            header.set_size(3);
            header.set_mode(0o755);
            builder.append_data(&mut header, name, &b"bin"[..]).unwrap();
        }
        builder.into_inner().unwrap().finish().unwrap();
    }
    runtime.install_engine_from(vec![archive], &tokio_util::sync::CancellationToken::new()).await.unwrap();
    // Linux CI stands in for macOS: the Python app names the same directory there.
    if !cfg!(windows) {
        let python_binary = python(json!({"command": "engine_binary", "directory": dir}));
        assert_eq!(Path::new(python_binary.as_str().unwrap()), runtime.binary().unwrap());
    }

    let model = &MODELS["lightonocr-3-0.8b"];
    let models = directory.path().join("models").join(&model.id);
    std::fs::create_dir_all(&models).unwrap();
    for file in &model.files {
        std::fs::File::create(models.join(&file.filename)).unwrap().set_len(file.size).unwrap();
    }
    python(json!({"command": "write_model_receipt", "directory": dir, "model": model.id}));
    assert!(runtime.installed(&model.id));
    assert_eq!(python(json!({"command": "model_installed", "directory": dir, "model": model.id})), json!(true));
    std::fs::OpenOptions::new()
        .append(true)
        .open(models.join(&model.files[0].filename))
        .unwrap()
        .write_all(b"x")
        .unwrap();
    assert!(!runtime.installed(&model.id));
}
