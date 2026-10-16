//! Verified installs and an owned, loopback-only `llama-server` process.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use base64::Engine as _;
use serde_json::{Map, Value, json};
use tokio::process::{Child, Command};
use tokio_util::sync::CancellationToken;

use crate::catalog::{Acceleration, ENGINE_VERSION, Model, Platform, engine_artifacts};
use crate::download::{Downloader, Progress};
use crate::error::{Error, Result};
use crate::extract::extract_engine;
use crate::fsutil::{mtime_ns, write_replace};
use crate::hardware::{HardwareReport, inspect_hardware, require_capacity};
use crate::http::Http;
use crate::models::ModelRegistry;
use crate::ocr::OcrProvider;
use crate::operation::Operation;

const LOAD_TIMEOUT: Duration = Duration::from_secs(180);
const STOP_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_RECEIPT: u64 = 524_288;

pub(crate) fn system_name() -> &'static str {
    match std::env::consts::OS {
        "macos" => "Darwin",
        "windows" => "Windows",
        "linux" => "Linux",
        other => other,
    }
}

const BINARY_NAME: &str = if cfg!(windows) { "llama-server.exe" } else { "llama-server" };

/// Runs the engine without a console window on Windows.
pub(crate) fn hide_window(command: &mut Command) {
    #[cfg(windows)]
    command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    #[cfg(not(windows))]
    let _ = command;
}

/// Two engines may run at once: one for OCR and one for review.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Session {
    Ocr,
    Review,
}

impl Session {
    fn key_filename(self) -> &'static str {
        match self {
            Session::Ocr => "session-key",
            Session::Review => "session-key-review",
        }
    }
}

#[derive(Debug, Clone)]
pub struct RuntimeOptions {
    /// Use this `llama-server` instead of the verified engine (custom builds, tests).
    pub engine: Option<PathBuf>,
    /// Check memory and GPU before loading a model.
    pub check_hardware: bool,
    pub http: Http,
    pub downloader_backoff: f64,
}

impl Default for RuntimeOptions {
    fn default() -> Self {
        Self { engine: None, check_hardware: true, http: Http::shared().clone(), downloader_backoff: 1.0 }
    }
}

/// The running engine: endpoint and key of a loaded, warmed-up model.
#[derive(Clone)]
pub struct Loaded {
    pub model_id: String,
    pub endpoint: String,
    pub key: String,
    pub acceleration: Acceleration,
}

impl std::fmt::Debug for Loaded {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Loaded")
            .field("model_id", &self.model_id)
            .field("endpoint", &self.endpoint)
            .finish_non_exhaustive()
    }
}

pub struct LocalRuntime {
    directory: PathBuf,
    session: Session,
    slots: u32,
    pub acceleration: Acceleration,
    models: ModelRegistry,
    options: RuntimeOptions,
    process: Option<Child>,
    #[cfg(windows)]
    job: Option<job::Job>,
    loaded: Option<Loaded>,
    pub hardware: Option<HardwareReport>,
}

impl LocalRuntime {
    pub fn new(
        directory: &Path,
        session: Session,
        slots: u32,
        acceleration: Acceleration,
        models: ModelRegistry,
    ) -> Result<Self> {
        Self::with_options(directory, session, slots, acceleration, models, RuntimeOptions::default())
    }

    pub fn with_options(
        directory: &Path,
        session: Session,
        slots: u32,
        acceleration: Acceleration,
        models: ModelRegistry,
        options: RuntimeOptions,
    ) -> Result<Self> {
        if !(1..=8).contains(&slots) {
            return Err(Error::invalid("Runtime slots must be between 1 and 8"));
        }
        std::fs::create_dir_all(directory).map_err(|_| Error::operation("Не удалось создать каталог данных"))?;
        let directory = std::fs::canonicalize(directory).unwrap_or_else(|_| directory.to_path_buf());
        Ok(Self {
            directory,
            session,
            slots,
            acceleration,
            models,
            options,
            process: None,
            #[cfg(windows)]
            job: None,
            loaded: None,
            hardware: None,
        })
    }

    pub fn models(&self) -> &ModelRegistry {
        &self.models
    }

    pub fn set_models(&mut self, models: ModelRegistry) {
        self.models = models;
    }

    fn model(&self, id: &str) -> Result<&Model> {
        self.models.get(id).ok_or_else(|| Error::operation("Неизвестная модель"))
    }

    pub fn engine_directory(&self) -> PathBuf {
        let suffix = match (cfg!(windows), self.acceleration) {
            (true, Acceleration::Gpu) => "-windows-cuda",
            (true, Acceleration::Cpu) => "-windows-cpu",
            (false, _) => "",
        };
        self.directory.join("runtime").join(format!("{ENGINE_VERSION}{suffix}"))
    }

    fn key_path(&self) -> PathBuf {
        self.directory.join("runtime").join(self.session.key_filename())
    }

    fn model_directory(&self, id: &str) -> PathBuf {
        self.directory.join("models").join(id)
    }

    /// The installed `llama-server`, if its files still match the install receipt.
    pub fn binary(&self) -> Option<PathBuf> {
        if let Some(engine) = &self.options.engine {
            return Some(engine.clone());
        }
        let root = self.engine_directory();
        let mut candidates: Vec<PathBuf> = walkdir::WalkDir::new(&root)
            .into_iter()
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_type().is_file() && entry.file_name() == BINARY_NAME)
            .map(|entry| entry.into_path())
            .collect();
        candidates.sort();
        let receipt = root.join("verified-engine.json");
        if receipt.exists() && !engine_receipt_matches(&root, &receipt) {
            return None;
        }
        candidates.into_iter().next()
    }

    /// True when every model file matches the hash receipt written at install.
    pub fn installed(&self, id: &str) -> bool {
        let Ok(model) = self.model(id) else {
            return false;
        };
        let directory = self.model_directory(id);
        let Ok(text) = std::fs::read(directory.join("verified.json")) else {
            return false;
        };
        let Ok(Value::Object(receipt)) = serde_json::from_slice::<Value>(&text) else {
            return false;
        };
        model.files.iter().all(|file| {
            let Ok(metadata) = std::fs::metadata(directory.join(&file.filename)) else {
                return false;
            };
            let Ok(modified) = mtime_ns(&metadata) else {
                return false;
            };
            metadata.len() == file.size
                && receipt.get(&file.filename)
                    == Some(&json!({"sha256": file.sha256, "size": file.size, "mtime_ns": modified}))
        })
    }

    /// Downloads and verifies the engine (once) and the model files.
    pub async fn install(&mut self, id: &str, cancel: &CancellationToken, progress: Progress<'_>) -> Result<()> {
        let model = self.model(id)?.clone();
        let mut downloader = Downloader::new(self.options.http.clone());
        downloader.backoff = self.options.downloader_backoff;
        if self.binary().is_none() {
            let artifacts = engine_artifacts(Platform::current(), self.acceleration)?;
            let mut archives = Vec::new();
            for artifact in &artifacts {
                let archive = self.directory.join("downloads").join(&artifact.filename);
                downloader.download(artifact, &archive, cancel, progress).await?;
                archives.push(archive);
            }
            progress("Установка движка", 0, 1);
            self.install_engine_from(archives, cancel).await?;
        }
        let directory = self.model_directory(id);
        for artifact in &model.files {
            downloader.download(artifact, &directory.join(&artifact.filename), cancel, progress).await?;
        }
        if cancel.is_cancelled() {
            return Err(Error::cancelled());
        }
        let mut receipt = Map::new();
        for file in &model.files {
            let metadata = std::fs::metadata(directory.join(&file.filename))
                .map_err(|_| Error::operation("Файл модели не найден"))?;
            let modified = mtime_ns(&metadata).map_err(|_| Error::operation("Файл модели не найден"))?;
            receipt
                .insert(file.filename.clone(), json!({"sha256": file.sha256, "size": file.size, "mtime_ns": modified}));
        }
        let encoded = crate::pyjson::dumps(&Value::Object(receipt));
        write_replace(&directory.join("verified.json"), encoded.as_bytes(), "json.tmp")
            .map_err(|_| Error::operation("Не удалось сохранить проверку модели"))
    }

    /// Installs the engine from already verified archives (downloaded by
    /// [`LocalRuntime::install`] or supplied for an offline install).
    pub async fn install_engine_from(&self, archives: Vec<PathBuf>, cancel: &CancellationToken) -> Result<()> {
        let root = self.engine_directory();
        let cancel = cancel.clone();
        tokio::task::spawn_blocking(move || install_engine(&root, &archives, &cancel))
            .await
            .map_err(|_| Error::operation("Не удалось установить движок"))??;
        if self.binary().is_none() {
            return Err(Error::operation("В пакете движка нет llama-server"));
        }
        Ok(())
    }

    pub async fn preflight(&mut self, id: &str, cancel: &CancellationToken) -> Result<HardwareReport> {
        let binary = self.binary();
        let report = inspect_hardware(binary.as_deref(), self.acceleration, cancel).await?;
        require_capacity(&report, self.model(id)?, self.slots)?;
        self.hardware = Some(report.clone());
        Ok(report)
    }

    /// The loaded model, while the engine process is alive.
    pub fn loaded(&mut self) -> Option<&Loaded> {
        let alive = self.process.as_mut().is_some_and(|process| matches!(process.try_wait(), Ok(None)));
        if !alive {
            self.loaded = None;
        }
        self.loaded.as_ref()
    }

    pub fn is_ready(&mut self) -> bool {
        self.loaded().is_some()
    }

    /// A provider for the loaded model, if `id` is the one loaded.
    pub fn provider(&mut self, id: &str, max_tokens: u32) -> Option<OcrProvider> {
        let mode = self.models.get(id)?.mode;
        let http = self.options.http.clone();
        let loaded = self.loaded().filter(|loaded| loaded.model_id == id)?;
        Some(
            OcrProvider::new(format!("{}/v1/chat/completions", loaded.endpoint), id, loaded.key.clone())
                .local()
                .mode(mode)
                .max_tokens(max_tokens)
                .with_http(http),
        )
    }

    /// Stops any running engine, then loads and warms up `id`. On any failure
    /// or cancellation nothing keeps running and the key file is removed.
    pub async fn start(&mut self, id: &str, cancel: &CancellationToken, progress: Progress<'_>) -> Result<()> {
        if cancel.is_cancelled() {
            return Err(Error::cancelled());
        }
        self.stop().await?;
        let result = self.launch(id, cancel, progress).await;
        if result.is_err() {
            // Report the original failure; a failed stop keeps ownership for a retry.
            let _ = self.stop().await;
        }
        result
    }

    async fn launch(&mut self, id: &str, cancel: &CancellationToken, progress: Progress<'_>) -> Result<()> {
        let binary = self.binary();
        let (Some(binary), true) = (binary, self.installed(id)) else {
            return Err(Error::operation("Сначала установите выбранную модель через настройки"));
        };
        let model = self.model(id)?.clone();
        if self.options.check_hardware {
            progress("Проверка оборудования и доступной памяти…", 0, 1);
            self.preflight(id, cancel).await?;
        }
        let port = {
            let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).map_err(|_| spawn_error())?;
            listener.local_addr().map_err(|_| spawn_error())?.port()
        };
        let key = session_key();
        let keyfile = self.key_path();
        std::fs::create_dir_all(keyfile.parent().unwrap()).map_err(|_| spawn_error())?;
        crate::keyfile::write_key(&keyfile, &key).map_err(|_| spawn_error())?;
        let directory = self.model_directory(id);
        let mut command = Command::new(&binary);
        command
            .arg("-m")
            .arg(directory.join(&model.weights().filename))
            .arg("--mmproj")
            .arg(directory.join(&model.projector().filename))
            .args(["--host", "127.0.0.1", "--port", &port.to_string()])
            .arg("--api-key-file")
            .arg(&keyfile)
            .args(["-c", &(8192 * self.slots).to_string()])
            .args(["-ngl", if self.acceleration == Acceleration::Gpu { "99" } else { "0" }])
            .args(["--parallel", &self.slots.to_string(), "--no-webui"]);
        if self.acceleration == Acceleration::Cpu {
            command.arg("--no-mmproj-offload");
        }
        command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).kill_on_drop(true);
        if let Some(parent) = binary.parent() {
            command.current_dir(parent);
        }
        hide_window(&mut command);
        if cancel.is_cancelled() {
            return Err(Error::cancelled());
        }
        let process = command.spawn().map_err(|_| spawn_error())?;
        #[cfg(windows)]
        {
            // The engine dies with the app, even if the app crashes.
            self.job = job::Job::kill_on_close().and_then(|job| job.assign(&process).map(|_| job)).ok();
        }
        self.process = Some(process);
        progress("Загрузка модели в память…", 0, 1);
        let endpoint = format!("http://127.0.0.1:{port}");
        self.wait_healthy(&endpoint, &key, cancel).await?;
        progress("Прогрев модели…", 0, 1);
        // Warm both text and vision with a small generated PNG before enabling OCR.
        let warm = Operation::with_budget(Duration::from_secs(120)).with_token(cancel.clone());
        OcrProvider::new(format!("{endpoint}/v1/chat/completions"), id, key.clone())
            .local()
            .mode(model.mode)
            .max_tokens(128)
            .with_http(self.options.http.clone())
            .recognize(&blank_png(), &warm)
            .await?;
        if cancel.is_cancelled() || self.exit_code().is_some() {
            return Err(Error::Cancelled("Подготовка модели отменена".into()));
        }
        self.loaded = Some(Loaded { model_id: id.to_string(), endpoint, key, acceleration: self.acceleration });
        Ok(())
    }

    fn exit_code(&mut self) -> Option<i32> {
        let status = self.process.as_mut()?.try_wait().ok()??;
        #[cfg(unix)]
        if let Some(signal) = std::os::unix::process::ExitStatusExt::signal(&status) {
            return Some(-signal);
        }
        Some(status.code().unwrap_or(-1))
    }

    async fn wait_healthy(&mut self, endpoint: &str, key: &str, cancel: &CancellationToken) -> Result<()> {
        let deadline = tokio::time::Instant::now() + LOAD_TIMEOUT;
        let health = format!("{endpoint}/health");
        while tokio::time::Instant::now() < deadline {
            if cancel.is_cancelled() {
                return Err(Error::cancelled());
            }
            if let Some(code) = self.exit_code() {
                return Err(Error::operation(format!("Движок завершился при загрузке (код {code})")));
            }
            let request = self.options.http.local.get(&health).bearer_auth(key).timeout(Duration::from_secs(2));
            if request.send().await.is_ok_and(|response| response.status() == 200) {
                return Ok(());
            }
            tokio::select! {
                _ = cancel.cancelled() => return Err(Error::cancelled()),
                _ = tokio::time::sleep(Duration::from_millis(250)) => {}
            }
        }
        Err(Error::operation("Модель не загрузилась за 180 секунд"))
    }

    /// Stops the engine. If it does not exit, ownership is kept so another
    /// stop can retry; the key file is removed only once nothing runs.
    pub async fn stop(&mut self) -> Result<()> {
        self.loaded = None;
        if let Some(process) = self.process.as_mut()
            && matches!(process.try_wait(), Ok(None))
        {
            terminate(process);
            if tokio::time::timeout(STOP_TIMEOUT, process.wait()).await.is_err() {
                let _ = process.start_kill();
                if tokio::time::timeout(STOP_TIMEOUT, process.wait()).await.is_err() {
                    return Err(Error::operation("Не удалось завершить движок. Повторите выгрузку модели."));
                }
            }
        }
        self.process = None;
        #[cfg(windows)]
        {
            self.job = None;
        }
        let _ = std::fs::remove_file(self.key_path());
        Ok(())
    }

    /// Removes a model's files, unloading it first.
    pub async fn delete(&mut self, id: &str) -> Result<()> {
        self.model(id)?;
        if self.loaded.as_ref().is_some_and(|loaded| loaded.model_id == id) {
            self.stop().await?;
        }
        let directory = self.model_directory(id);
        if directory.exists() {
            std::fs::remove_dir_all(&directory)
                .map_err(|_| Error::operation("Не удалось удалить модель. Выгрузите её и повторите удаление."))?;
        }
        Ok(())
    }
}

impl Drop for LocalRuntime {
    fn drop(&mut self) {
        if let Some(process) = self.process.as_mut() {
            let _ = process.start_kill();
        }
        let _ = std::fs::remove_file(self.key_path());
    }
}

fn spawn_error() -> Error {
    Error::operation("Не удалось запустить движок. Проверьте установку локальной модели.")
}

/// Like Python's `secrets.token_urlsafe(32)`.
fn session_key() -> String {
    let bytes: [u8; 32] = rand::random();
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(unix)]
fn terminate(process: &mut Child) {
    if let Some(pid) = process.id() {
        unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGTERM);
        }
    }
}

#[cfg(not(unix))]
fn terminate(process: &mut Child) {
    let _ = process.start_kill();
}

fn engine_receipt_matches(root: &Path, receipt: &Path) -> bool {
    let Ok(metadata) = std::fs::metadata(receipt) else {
        return false;
    };
    if metadata.len() > MAX_RECEIPT {
        return false;
    }
    let Ok(Ok(Value::Object(files))) = std::fs::read(receipt).map(|data| serde_json::from_slice::<Value>(&data)) else {
        return false;
    };
    let Ok(canonical_root) = std::fs::canonicalize(root) else {
        return false;
    };
    !files.is_empty()
        && files.iter().all(|(name, record)| {
            let path = root.join(name);
            let inside = std::fs::canonicalize(&path).is_ok_and(|p| p.starts_with(&canonical_root));
            let Ok(metadata) = std::fs::metadata(&path) else {
                return false;
            };
            let Ok(modified) = mtime_ns(&metadata) else {
                return false;
            };
            inside && *record == json!({"size": metadata.len(), "mtime_ns": modified})
        })
}

/// Extracts archives into a staging directory, records a receipt and swaps it in.
fn install_engine(root: &Path, archives: &[PathBuf], cancel: &CancellationToken) -> Result<()> {
    let io = |_| Error::operation("Не удалось установить движок");
    let parent = root.parent().unwrap();
    std::fs::create_dir_all(parent).map_err(io)?;
    let staging = tempfile::Builder::new().prefix("tmp").tempdir_in(parent).map_err(io)?;
    for archive in archives {
        extract_engine(archive, staging.path(), cancel)?;
    }
    if cancel.is_cancelled() {
        return Err(Error::cancelled());
    }
    let mut receipt = Map::new();
    let mut found = false;
    for entry in walkdir::WalkDir::new(staging.path()).sort_by_file_name() {
        let entry = entry.map_err(|_| Error::operation("Не удалось установить движок"))?;
        if !entry.file_type().is_file() {
            continue;
        }
        found |= entry.file_name() == BINARY_NAME;
        let metadata = entry.metadata().map_err(|_| Error::operation("Не удалось установить движок"))?;
        let relative = entry.path().strip_prefix(staging.path()).unwrap();
        let name = relative.components().map(|c| c.as_os_str().to_string_lossy()).collect::<Vec<_>>().join("/");
        receipt.insert(name, json!({"size": metadata.len(), "mtime_ns": mtime_ns(&metadata).map_err(io)?}));
    }
    if !found {
        return Err(Error::operation(format!("В пакете движка нет {BINARY_NAME}")));
    }
    std::fs::write(staging.path().join("verified-engine.json"), crate::pyjson::dumps(&Value::Object(receipt)))
        .map_err(io)?;
    let _ = std::fs::remove_dir_all(root);
    let staged = staging.keep();
    std::fs::rename(&staged, root).map_err(io)?;
    #[cfg(unix)]
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(|e| e.ok()) {
        if entry.file_name() == BINARY_NAME {
            use std::os::unix::fs::PermissionsExt;
            let mode =
                entry.metadata().map_err(|_| Error::operation("Не удалось установить движок"))?.permissions().mode();
            std::fs::set_permissions(entry.path(), std::fs::Permissions::from_mode(mode | 0o100)).map_err(io)?;
        }
    }
    Ok(())
}

/// A 96×96 white RGB PNG for warming up the vision encoder.
pub fn blank_png() -> Vec<u8> {
    let mut output = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut output, 96, 96);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().expect("png header");
        writer.write_image_data(&[0xff; 96 * 96 * 3]).expect("png data");
    }
    output
}

#[cfg(windows)]
mod job {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation, SetInformationJobObject,
    };

    /// A job object that kills its processes when the last handle closes.
    pub struct Job(HANDLE);

    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn kill_on_close() -> std::io::Result<Self> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err(std::io::Error::last_os_error());
                }
                let job = Job(handle);
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as *const _,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) == 0
                {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(job)
            }
        }

        pub fn assign(&self, process: &tokio::process::Child) -> std::io::Result<()> {
            let handle = process.raw_handle().ok_or_else(|| std::io::Error::other("process exited"))?;
            if unsafe { AssignProcessToJobObject(self.0, handle as HANDLE) } == 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
