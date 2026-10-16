//! Extraction of verified engine packages inside a staging directory, with
//! traversal and link checks.

use std::fs::File;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use tokio_util::sync::CancellationToken;

use crate::error::{Error, Result};
use crate::fsutil::free_space;

fn unsafe_path() -> Error {
    Error::operation("Небезопасный путь в архиве движка")
}

fn check(cancel: &CancellationToken) -> Result<()> {
    if cancel.is_cancelled() { Err(Error::cancelled()) } else { Ok(()) }
}

/// Lexically joins a relative archive path under `root`, rejecting absolute
/// paths, `.`/`..`, drive letters and names Windows would silently alter.
fn contained(root: &Path, name: &str) -> Result<PathBuf> {
    let name = name.replace('\\', "/");
    let parts: Vec<&str> = name.split('/').filter(|part| !part.is_empty()).collect();
    if name.starts_with('/') || parts.is_empty() {
        return Err(unsafe_path());
    }
    let mut target = root.to_path_buf();
    for part in parts {
        if matches!(part, "." | "..") || part.contains(':') || part.trim_end_matches([' ', '.']) != part {
            return Err(unsafe_path());
        }
        target.push(part);
    }
    Ok(target)
}

/// Where a symbolic link inside the archive points, if it stays inside `root`.
fn link_target(root: &Path, link: &Path, target: &Path) -> Result<()> {
    if target.is_absolute() {
        return Err(unsafe_path());
    }
    let mut resolved = link.parent().unwrap_or(root).to_path_buf();
    for component in target.components() {
        match component {
            Component::Normal(part) => resolved.push(part),
            Component::CurDir => {}
            Component::ParentDir => {
                if !resolved.pop() || !resolved.starts_with(root) {
                    return Err(unsafe_path());
                }
            }
            _ => return Err(unsafe_path()),
        }
    }
    if resolved.starts_with(root) { Ok(()) } else { Err(unsafe_path()) }
}

pub fn extract_engine(archive: &Path, destination: &Path, cancel: &CancellationToken) -> Result<()> {
    check(cancel)?;
    if archive.extension().is_some_and(|extension| extension == "zip") {
        extract_zip(archive, destination, cancel)
    } else {
        extract_tar(archive, destination, cancel)
    }
}

fn extract_tar(archive: &Path, destination: &Path, cancel: &CancellationToken) -> Result<()> {
    let io = |_| Error::operation("Не удалось распаковать движок");
    let file = File::open(archive).map_err(io)?;
    let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(file));
    tar.set_preserve_permissions(false);
    tar.set_unpack_xattrs(false);
    for entry in tar.entries().map_err(io)? {
        check(cancel)?;
        let mut entry = entry.map_err(io)?;
        let name = entry.path().map_err(io)?.to_string_lossy().into_owned();
        let target = contained(destination, &name)?;
        let kind = entry.header().entry_type();
        // Same policy as Python's tarfile "data" filter: regular files,
        // directories and links that stay inside the destination.
        if kind.is_symlink() || kind.is_hard_link() {
            let link = entry.link_name().map_err(io)?.ok_or_else(unsafe_path)?;
            if kind.is_hard_link() {
                contained(destination, &link.to_string_lossy())?;
            } else {
                link_target(destination, &target, &link)?;
            }
        } else if !(kind.is_file() || kind.is_dir() || kind.is_contiguous()) {
            return Err(unsafe_path());
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(io)?;
        }
        if !entry.unpack_in(destination).map_err(io)? {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

fn extract_zip(archive: &Path, destination: &Path, cancel: &CancellationToken) -> Result<()> {
    fn io<E>(_: E) -> Error {
        Error::operation("Не удалось распаковать движок")
    }
    let mut package = zip::ZipArchive::new(File::open(archive).map_err(io)?).map_err(io)?;
    let mut total = 0u64;
    for index in 0..package.len() {
        total += package.by_index_raw(index).map_err(io)?.size();
    }
    if free_space(destination).unwrap_or(u64::MAX) < total + 64 * 1024 * 1024 {
        return Err(Error::operation("Недостаточно места для распаковки движка"));
    }
    let mut buffer = vec![0; 1024 * 1024];
    for index in 0..package.len() {
        check(cancel)?;
        let mut file = package.by_index(index).map_err(io)?;
        let target = contained(destination, &file.name().map_err(|_| unsafe_path())?)?;
        if file.unix_mode().is_some_and(|mode| mode & 0o170000 == 0o120000) {
            return Err(unsafe_path());
        }
        if file.is_dir() {
            std::fs::create_dir_all(&target).map_err(io)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(io)?;
        }
        let mut output = File::create(&target).map_err(io)?;
        loop {
            check(cancel)?;
            let read = file.read(&mut buffer).map_err(io)?;
            if read == 0 {
                break;
            }
            output.write_all(&buffer[..read]).map_err(io)?;
        }
    }
    Ok(())
}
