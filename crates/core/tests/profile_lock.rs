use ctrlcctrlv_core::profile_lock::ProfileLock;

#[test]
fn lock_excludes_other_handles_and_releases_without_removing_inode() {
    let directory = tempfile::tempdir().unwrap();
    let guard = ProfileLock::acquire(directory.path()).unwrap();
    assert_eq!(ProfileLock::acquire(directory.path()).err().unwrap().kind(), std::io::ErrorKind::WouldBlock);
    drop(guard);
    assert!(directory.path().join(".profile.lock").exists());
    assert!(ProfileLock::acquire(directory.path()).is_ok());
}
