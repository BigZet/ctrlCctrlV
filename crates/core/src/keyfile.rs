//! The loopback engine's per-session API key file, restricted before any
//! secret is written to it.

use std::fs::File;
use std::io::Write;
use std::path::Path;

/// Creates or truncates `path` readable only by the owner (plus
/// administrators and SYSTEM on Windows), then writes `key`.
pub fn write_key(path: &Path, key: &str) -> std::io::Result<()> {
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    let mut file: File = options.open(path)?;
    protect(path, &file)?;
    file.write_all(key.as_bytes())?;
    file.sync_all()
}

#[cfg(unix)]
fn protect(_path: &Path, file: &File) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    // An existing file keeps its old mode on open; restrict it explicitly.
    file.set_permissions(std::fs::Permissions::from_mode(0o600))
}

#[cfg(windows)]
fn protect(path: &Path, _file: &File) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Authorization::{
        ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
    };
    use windows_sys::Win32::Security::{
        DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, SetFileSecurityW,
    };

    // Protected DACL: owner, administrators and SYSTEM; no inherited grants.
    let sddl: Vec<u16> = "D:P(A;;FA;;;OW)(A;;FA;;;BA)(A;;FA;;;SY)".encode_utf16().chain([0]).collect();
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    unsafe {
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            std::ptr::null_mut(),
        ) == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        let result = SetFileSecurityW(
            wide.as_ptr(),
            PROTECTED_DACL_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            descriptor,
        );
        let error = std::io::Error::last_os_error();
        LocalFree(descriptor as _);
        if result == 0 { Err(error) } else { Ok(()) }
    }
}
