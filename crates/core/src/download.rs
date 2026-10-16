//! Verified, resumable downloads of pinned artifacts.

use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_util::sync::CancellationToken;

use crate::catalog::Artifact;
use crate::error::{Error, Result};
use crate::fsutil::free_space;
use crate::http::Http;

const MIB: u64 = 1024 * 1024;
/// Free space kept on top of the file itself.
const RESERVE: u64 = 64 * MIB;
/// Bounded ranges let large packages through buffering proxies without
/// waiting for the entire archive before the first byte.
const RANGE: u64 = 16 * MIB;
const TIMEOUT: Duration = Duration::from_secs(30);
const RETRIES: u32 = 3;

/// `progress(label, done, total)`.
pub type Progress<'a> = &'a (dyn Fn(&str, u64, u64) + Send + Sync);

fn check(cancel: &CancellationToken) -> Result<()> {
    if cancel.is_cancelled() { Err(Error::cancelled()) } else { Ok(()) }
}

pub async fn file_hash(path: &Path, cancel: &CancellationToken) -> Result<String> {
    let io = |_| Error::operation("Не удалось прочитать загруженный файл");
    let mut file = tokio::fs::File::open(path).await.map_err(io)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0; 4 * MIB as usize];
    loop {
        check(cancel)?;
        let read = file.read(&mut buffer).await.map_err(io)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    check(cancel)?;
    Ok(hex::encode(digest.finalize()))
}

fn part_path(destination: &Path) -> PathBuf {
    let mut name = destination.file_name().unwrap_or_default().to_os_string();
    name.push(".part");
    destination.with_file_name(name)
}

fn size_of(path: &Path) -> u64 {
    std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

/// Why one attempt stopped.
enum Attempt {
    /// Worth retrying from the bytes already written, after `delay`.
    Retry(Duration),
    Fatal(Error),
}

impl From<Error> for Attempt {
    fn from(error: Error) -> Self {
        Attempt::Fatal(error)
    }
}

static CONTENT_RANGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\Abytes (\d+)-(\d+)/(\d+)\z").unwrap());

pub struct Downloader {
    http: Http,
    /// Multiplies retry delays; tests set it to zero.
    pub backoff: f64,
}

impl Default for Downloader {
    fn default() -> Self {
        Self { http: Http::shared().clone(), backoff: 1.0 }
    }
}

impl Downloader {
    pub fn new(http: Http) -> Self {
        Self { http, backoff: 1.0 }
    }

    /// Downloads into `destination.part`, resuming it when possible, and moves
    /// it to `destination` only after the SHA256 matches the catalog.
    pub async fn download(
        &self,
        artifact: &Artifact,
        destination: &Path,
        cancel: &CancellationToken,
        progress: Progress<'_>,
    ) -> Result<()> {
        check(cancel)?;
        let parent = destination.parent().unwrap_or(Path::new("."));
        tokio::fs::create_dir_all(parent).await.map_err(|_| Error::operation("Не удалось создать каталог загрузки"))?;
        let part = part_path(destination);
        if size_of(destination) == artifact.size
            && destination.exists()
            && file_hash(destination, cancel).await? == artifact.sha256
        {
            progress(&artifact.filename, artifact.size, artifact.size);
            return Ok(());
        }
        let mut offset = size_of(&part);
        if offset > artifact.size {
            let _ = std::fs::remove_file(&part);
            offset = 0;
        }
        if free_space(parent).unwrap_or(u64::MAX) < artifact.size.saturating_sub(offset) + RESERVE {
            return Err(Error::operation("Недостаточно места для загрузки модели"));
        }
        check(cancel)?;
        let mut attempt = 0;
        while size_of(&part) != artifact.size {
            check(cancel)?;
            match self.attempt(artifact, &part, parent, cancel, progress).await {
                Ok(()) => attempt = 0,
                Err(Attempt::Fatal(error)) => return Err(error),
                Err(Attempt::Retry(delay)) => {
                    check(cancel)?;
                    if attempt == RETRIES {
                        return Err(Error::operation(
                            "Соединение прервано. Загруженная часть сохранена; повторите загрузку.",
                        ));
                    }
                    progress(
                        &format!("Повтор подключения {}/{RETRIES} · загруженная часть сохранена", attempt + 1),
                        size_of(&part),
                        artifact.size,
                    );
                    tokio::select! {
                        _ = cancel.cancelled() => return Err(Error::cancelled()),
                        _ = tokio::time::sleep(delay.mul_f64(self.backoff)) => {}
                    }
                    attempt += 1;
                }
            }
        }
        progress(&format!("Проверка SHA256: {}", artifact.filename), artifact.size, artifact.size);
        if file_hash(&part, cancel).await? != artifact.sha256 {
            let _ = std::fs::remove_file(&part);
            return Err(Error::operation("Контрольная сумма не совпала. Файл удалён; повторите загрузку."));
        }
        check(cancel)?;
        std::fs::rename(&part, destination).map_err(|_| Error::operation("Не удалось сохранить загруженный файл"))
    }

    /// One request for the next range, appended to the partial file.
    async fn attempt(
        &self,
        artifact: &Artifact,
        part: &Path,
        parent: &Path,
        cancel: &CancellationToken,
        progress: Progress<'_>,
    ) -> Result<(), Attempt> {
        let mut retry_delay = Duration::from_secs_f64(0.5);
        let mut offset = size_of(part);
        let mut range_end = artifact.size - 1;
        let mut request = self.http.remote.get(&artifact.url).header("Accept-Encoding", "identity");
        if artifact.size > 2 * RANGE {
            range_end = (artifact.size - 1).min(offset + RANGE - 1);
            request = request.header("Range", format!("bytes={offset}-{range_end}"));
        } else if offset > 0 {
            request = request.header("Range", format!("bytes={offset}-"));
        }
        let response = tokio::select! {
            _ = cancel.cancelled() => return Err(Error::cancelled().into()),
            sent = tokio::time::timeout(TIMEOUT, request.send()) => match sent {
                Ok(Ok(response)) => response,
                _ => return Err(Attempt::Retry(retry_delay)),
            },
        };
        // A cancellation while waiting for headers must not discard resumable
        // bytes when the server ignores Range.
        check(cancel)?;
        let status = response.status().as_u16();
        if matches!(status, 408 | 425 | 429 | 500 | 502 | 503 | 504) {
            let retry_after = response.headers().get("Retry-After").and_then(|v| v.to_str().ok()).unwrap_or("");
            if !retry_after.is_empty() && retry_after.bytes().all(|b| b.is_ascii_digit()) {
                let seconds: f64 = retry_after.parse().unwrap_or(0.0);
                retry_delay = Duration::from_secs_f64(seconds.max(retry_delay.as_secs_f64()).min(10.0));
            }
            return Err(Attempt::Retry(retry_delay));
        }
        if status != 200 && status != 206 {
            return Err(Error::operation(format!("Ошибка загрузки HTTP {status}")).into());
        }
        if status == 206 {
            let header = response.headers().get("Content-Range").and_then(|v| v.to_str().ok()).unwrap_or("");
            let valid = CONTENT_RANGE.captures(header).is_some_and(|m| {
                let number = |i: usize| m[i].parse::<u64>().ok();
                number(1) == Some(offset) && number(3) == Some(artifact.size) && number(2) == Some(range_end)
            });
            if !valid {
                return Err(Error::operation("Сервер вернул неверный диапазон загрузки").into());
            }
        } else {
            // The server ignored Range. A full response needs more free space
            // before it replaces the partial file.
            if free_space(parent).unwrap_or(u64::MAX).saturating_add(offset) < artifact.size + RESERVE {
                return Err(Error::operation("Недостаточно места для загрузки модели").into());
            }
            offset = 0;
            range_end = artifact.size - 1;
        }
        let io = |_| Attempt::Fatal(Error::operation("Не удалось записать загружаемый файл"));
        let mut file = tokio::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .append(offset > 0)
            .truncate(offset == 0)
            .open(part)
            .await
            .map_err(io)?;
        progress(&artifact.filename, offset, artifact.size);
        let mut response = response;
        loop {
            let chunk = tokio::select! {
                _ = cancel.cancelled() => {
                    let _ = file.flush().await;
                    return Err(Error::cancelled().into());
                }
                chunk = tokio::time::timeout(TIMEOUT, response.chunk()) => chunk,
            };
            let chunk = match chunk {
                Ok(Ok(Some(chunk))) => chunk,
                Ok(Ok(None)) => break,
                _ => {
                    file.flush().await.map_err(io)?;
                    return Err(Attempt::Retry(retry_delay));
                }
            };
            if offset + chunk.len() as u64 > range_end + 1 {
                return Err(Error::operation("Размер файла не совпадает с каталогом").into());
            }
            file.write_all(&chunk).await.map_err(io)?;
            offset += chunk.len() as u64;
            progress(&artifact.filename, offset, artifact.size);
        }
        file.flush().await.map_err(io)?;
        if offset != range_end + 1 {
            return Err(Attempt::Retry(retry_delay));
        }
        Ok(())
    }
}
