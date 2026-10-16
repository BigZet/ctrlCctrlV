//! Small filesystem helpers shared by the modules that persist profile data.

use std::io;
use std::path::Path;
use std::time::UNIX_EPOCH;

/// Writes `path.<suffix>` and renames it over `path`, so readers never see a partial file.
pub(crate) fn write_replace(path: &Path, contents: &[u8], suffix: &str) -> io::Result<()> {
    let temporary = path.with_extension(suffix);
    std::fs::write(&temporary, contents)?;
    std::fs::rename(&temporary, path)
}

/// Modification time in nanoseconds, equal to Python's `os.stat().st_mtime_ns`
/// on Windows (100 ns FILETIME ticks) and macOS (timespec).
pub(crate) fn mtime_ns(metadata: &std::fs::Metadata) -> io::Result<i64> {
    let modified = metadata.modified()?;
    Ok(match modified.duration_since(UNIX_EPOCH) {
        Ok(after) => after.as_nanos() as i64,
        Err(before) => -(before.duration().as_nanos() as i64),
    })
}

/// Free bytes on the volume holding `path`.
pub(crate) fn free_space(path: &Path) -> io::Result<u64> {
    fs4::available_space(path)
}
