use std::io::Write;
use std::path::{Path, PathBuf};

use ctrlcctrlv_core::catalog::{Acceleration, Artifact, Model, PromptMode};
use ctrlcctrlv_core::models::ModelRegistry;
use ctrlcctrlv_core::operation::Operation;
use ctrlcctrlv_core::runtime::{LocalRuntime, RuntimeOptions, Session};
use tokio_util::sync::CancellationToken;

const MODEL: &str = "custom-0123456789abcdef";

fn quiet(_: &str, _: u64, _: u64) {}

fn fake_engine() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_fake-llama-server"))
}

/// A profile with one small "installed" model, as `install` would leave it.
fn profile(directory: &Path) -> ModelRegistry {
    profile_with_size(directory, 4)
}

fn profile_with_size(directory: &Path, size: u64) -> ModelRegistry {
    std::fs::create_dir_all(directory).unwrap();
    let file = |name: &str| Artifact {
        filename: name.into(),
        url: format!("https://example.com/{name}"),
        sha256: "a".repeat(64),
        size,
    };
    let model = Model {
        id: MODEL.into(),
        label: "Test".into(),
        files: vec![file("model.gguf"), file("mmproj.gguf")],
        source: "https://example.com".into(),
        license: "MIT".into(),
        mode: PromptMode::Json,
    };
    let mut registry = ModelRegistry::load(directory);
    registry.add(model.clone()).unwrap();
    let models = directory.join("models").join(MODEL);
    std::fs::create_dir_all(&models).unwrap();
    let mut receipt = serde_json::Map::new();
    for file in &model.files {
        let path = models.join(&file.filename);
        std::fs::File::create(&path).unwrap().set_len(size).unwrap();
        let modified =
            std::fs::metadata(&path).unwrap().modified().unwrap().duration_since(std::time::UNIX_EPOCH).unwrap();
        receipt.insert(
            file.filename.clone(),
            serde_json::json!({"sha256": file.sha256, "size": size, "mtime_ns": modified.as_nanos() as u64}),
        );
    }
    std::fs::write(models.join("verified.json"), serde_json::Value::Object(receipt).to_string()).unwrap();
    registry
}

fn runtime(directory: &Path, engine: PathBuf, check_hardware: bool) -> LocalRuntime {
    let options = RuntimeOptions { engine: Some(engine), check_hardware, ..RuntimeOptions::default() };
    LocalRuntime::with_options(directory, Session::Ocr, 1, Acceleration::Gpu, profile(directory), options).unwrap()
}

#[tokio::test]
async fn starts_warms_serves_and_stops_an_owned_engine() {
    let directory = tempfile::tempdir().unwrap();
    let mut runtime = runtime(directory.path(), fake_engine(), true);
    assert!(runtime.installed(MODEL));
    let labels = std::sync::Mutex::new(Vec::new());
    let progress = |label: &str, _: u64, _: u64| labels.lock().unwrap().push(label.to_string());
    runtime.start(MODEL, &CancellationToken::new(), &progress).await.unwrap();
    assert_eq!(
        *labels.lock().unwrap(),
        ["Проверка оборудования и доступной памяти…", "Загрузка модели в память…", "Прогрев модели…"]
    );
    assert_eq!(runtime.hardware.as_ref().unwrap().devices[0].name, "Fake GPU");
    let key = directory.path().join("runtime/session-key");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&key).unwrap().permissions().mode() & 0o777, 0o600);
    }
    let loaded = runtime.loaded().unwrap().clone();
    assert!(loaded.endpoint.starts_with("http://127.0.0.1:"));
    assert_eq!(std::fs::read_to_string(&key).unwrap(), loaded.key);
    assert_eq!(loaded.key.len(), 43);
    let provider = runtime.provider(MODEL, 256).unwrap();
    assert_eq!(provider.recognize(b"png", &Operation::new()).await.unwrap(), "warm");
    assert!(runtime.provider("qwen3-vl-4b", 256).is_none());

    // A cancelled start leaves the loaded model alone.
    let cancelled = CancellationToken::new();
    cancelled.cancel();
    assert!(runtime.start(MODEL, &cancelled, &quiet).await.unwrap_err().is_cancelled());
    assert!(runtime.is_ready());

    runtime.stop().await.unwrap();
    assert!(!runtime.is_ready());
    assert!(!key.exists());
    assert!(reqwest::get(format!("{}/health", loaded.endpoint)).await.is_err());
}

#[tokio::test]
async fn crashing_child_is_reaped_and_key_removed() {
    let directory = tempfile::tempdir().unwrap();
    let profile = directory.path().join("crash-profile");
    let mut runtime = runtime(&profile, fake_engine(), false);
    let error = runtime.start(MODEL, &CancellationToken::new(), &quiet).await.unwrap_err();
    assert_eq!(error.to_string(), "Движок завершился при загрузке (код 3)");
    assert!(!runtime.is_ready());
    assert!(!profile.join("runtime/session-key").exists());
}

#[tokio::test]
async fn spawn_failure_removes_a_restricted_key() {
    let directory = tempfile::tempdir().unwrap();
    let key = directory.path().join("runtime/session-key");
    std::fs::create_dir_all(key.parent().unwrap()).unwrap();
    std::fs::write(&key, "old").unwrap();
    let mut runtime = runtime(directory.path(), directory.path().join("missing-llama-server"), false);
    let error = runtime.start(MODEL, &CancellationToken::new(), &quiet).await.unwrap_err();
    assert_eq!(error.to_string(), "Не удалось запустить движок. Проверьте установку локальной модели.");
    assert!(!key.exists());
}

#[tokio::test]
async fn cancel_during_loading_stops_the_engine() {
    let directory = tempfile::tempdir().unwrap();
    let mut runtime = runtime(directory.path(), fake_engine(), false);
    let cancel = CancellationToken::new();
    let progress = {
        let cancel = cancel.clone();
        move |label: &str, _: u64, _: u64| {
            if label.starts_with("Загрузка") {
                cancel.cancel();
            }
        }
    };
    assert!(runtime.start(MODEL, &cancel, &progress).await.unwrap_err().is_cancelled());
    assert!(!runtime.is_ready());
    assert!(!directory.path().join("runtime/session-key").exists());
}

#[tokio::test]
async fn uninstalled_or_tampered_models_are_not_started() {
    let directory = tempfile::tempdir().unwrap();
    let mut runtime = runtime(directory.path(), fake_engine(), false);
    std::fs::OpenOptions::new()
        .append(true)
        .open(directory.path().join("models").join(MODEL).join("model.gguf"))
        .unwrap()
        .write_all(b"x")
        .unwrap();
    assert!(!runtime.installed(MODEL));
    let error = runtime.start(MODEL, &CancellationToken::new(), &quiet).await.unwrap_err();
    assert_eq!(error.to_string(), "Сначала установите выбранную модель через настройки");
    assert!(!runtime.installed("qwen3-vl-4b"));
}

#[tokio::test]
async fn insufficient_memory_is_reported_before_loading() {
    let directory = tempfile::tempdir().unwrap();
    // Sparse 20 GiB files: the estimate exceeds the fake GPU's 23000 MiB.
    let models = profile_with_size(directory.path(), 20 * 1024 * 1024 * 1024);
    let options = RuntimeOptions { engine: Some(fake_engine()), ..RuntimeOptions::default() };
    let mut runtime =
        LocalRuntime::with_options(directory.path(), Session::Ocr, 1, Acceleration::Gpu, models, options).unwrap();
    let error = runtime.start(MODEL, &CancellationToken::new(), &quiet).await.unwrap_err();
    assert!(matches!(error, ctrlcctrlv_core::Error::InsufficientMemory(_)), "{error:?}");
    assert!(error.to_string().starts_with("Недостаточно памяти GPU"));
}

#[tokio::test]
async fn delete_unloads_and_removes_model_files() {
    let directory = tempfile::tempdir().unwrap();
    let mut runtime = runtime(directory.path(), fake_engine(), false);
    runtime.start(MODEL, &CancellationToken::new(), &quiet).await.unwrap();
    runtime.delete(MODEL).await.unwrap();
    assert!(!runtime.is_ready());
    assert!(!directory.path().join("models").join(MODEL).exists());
    assert!(runtime.delete("unknown").await.is_err());
}

#[test]
fn invalid_slot_counts_are_rejected() {
    let directory = tempfile::tempdir().unwrap();
    for slots in [0, 9] {
        let error =
            LocalRuntime::new(directory.path(), Session::Review, slots, Acceleration::Cpu, ModelRegistry::builtin());
        assert!(error.is_err());
    }
}

fn tar_gz(path: &Path, entries: &[(&str, &[u8])]) {
    let file = std::fs::File::create(path).unwrap();
    let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(file, flate2::Compression::fast()));
    for (name, data) in entries {
        let mut header = tar::Header::new_gnu();
        header.set_size(data.len() as u64);
        header.set_mode(0o644);
        header.set_entry_type(tar::EntryType::Regular);
        builder.append_data(&mut header, name, *data).unwrap();
    }
    builder.into_inner().unwrap().finish().unwrap();
}

#[tokio::test]
async fn engine_install_records_a_receipt_that_detects_changes() {
    let directory = tempfile::tempdir().unwrap();
    let archive = directory.path().join("llama.tar.gz");
    let name = if cfg!(windows) { "llama-server.exe" } else { "llama-server" };
    tar_gz(&archive, &[(&format!("build/bin/{name}"), b"binary"), ("build/bin/libllama.dylib", b"lib")]);
    let runtime =
        LocalRuntime::new(directory.path(), Session::Ocr, 1, Acceleration::Gpu, ModelRegistry::builtin()).unwrap();
    runtime.install_engine_from(vec![archive], &CancellationToken::new()).await.unwrap();
    let binary = runtime.binary().unwrap();
    assert!(binary.ends_with(format!("build/bin/{name}")));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_ne!(std::fs::metadata(&binary).unwrap().permissions().mode() & 0o100, 0);
    }
    let receipt: serde_json::Value =
        serde_json::from_slice(&std::fs::read(runtime.engine_directory().join("verified-engine.json")).unwrap())
            .unwrap();
    assert_eq!(receipt["build/bin/libllama.dylib"]["size"], 3);
    std::fs::write(runtime.engine_directory().join("build/bin/libllama.dylib"), b"changed").unwrap();
    assert!(runtime.binary().is_none());
}

#[tokio::test]
async fn engine_archive_without_server_or_with_traversal_is_rejected() {
    let directory = tempfile::tempdir().unwrap();
    let runtime =
        LocalRuntime::new(directory.path(), Session::Ocr, 1, Acceleration::Gpu, ModelRegistry::builtin()).unwrap();
    let empty = directory.path().join("empty.tar.gz");
    tar_gz(&empty, &[("README", b"x")]);
    let error = runtime.install_engine_from(vec![empty], &CancellationToken::new()).await.unwrap_err();
    assert!(error.to_string().starts_with("В пакете движка нет"));

    let zip_path = directory.path().join("evil.zip");
    {
        let mut zip = zip::ZipWriter::new(std::fs::File::create(&zip_path).unwrap());
        zip.start_file("../evil.dll", zip::write::SimpleFileOptions::default()).unwrap();
        zip.write_all(b"x").unwrap();
        zip.finish().unwrap();
    }
    let error = runtime.install_engine_from(vec![zip_path], &CancellationToken::new()).await.unwrap_err();
    assert_eq!(error.to_string(), "Небезопасный путь в архиве движка");
    assert!(!directory.path().join("runtime/evil.dll").exists());
    assert!(runtime.binary().is_none());
}
