//! A persistent lock inode shared with Python; closing the handle releases it.
use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;

pub struct ProfileLock {
    _file: File,
}

impl ProfileLock {
    pub fn acquire(directory: &Path) -> io::Result<Self> {
        std::fs::create_dir_all(directory)?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(directory.join(".profile.lock"))?;
        fs4::FileExt::try_lock(&file).map_err(|e| match e {
            fs4::TryLockError::WouldBlock => io::Error::from(io::ErrorKind::WouldBlock),
            fs4::TryLockError::Error(error) => error,
        })?;
        Ok(Self { _file: file })
    }
}
