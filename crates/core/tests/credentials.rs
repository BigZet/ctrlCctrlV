use std::sync::Arc;

use ctrlcctrlv_core::credentials::{Credentials, MemoryStore, SecretStore};

#[test]
fn empty_value_removes_a_stored_key() {
    let store = Arc::new(MemoryStore::default());
    let credentials = Credentials::new(store.clone());
    assert_eq!(credentials.read().unwrap(), "");
    credentials.write("sk-or-test").unwrap();
    assert_eq!(credentials.read().unwrap(), "sk-or-test");
    credentials.write("").unwrap();
    assert_eq!(store.get().unwrap(), None);
    // Removing an absent key is not an error.
    credentials.write("").unwrap();
}

/// Uses the real Windows Credential Manager or macOS keychain and the Python
/// `keyring` package. Opt-in with `CTRLCCTRLV_KEYRING_TEST=1`.
#[test]
fn system_store_shares_the_key_with_python_keyring() {
    if std::env::var_os("CTRLCCTRLV_KEYRING_TEST").is_none() {
        eprintln!("skipped: set CTRLCCTRLV_KEYRING_TEST=1");
        return;
    }
    let python = std::env::var("CTRLCCTRLV_PYTHON").unwrap_or_else(|_| "python".into());
    let keyring = |code: &str| {
        let output = std::process::Command::new(&python)
            .args(["-c", &format!("import keyring\n{code}")])
            .output()
            .expect("python");
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8(output.stdout).unwrap().trim().to_string()
    };
    let credentials = Credentials::system().unwrap();
    let previous = credentials.read().unwrap();
    credentials.write("from-rust").unwrap();
    assert_eq!(keyring("print(keyring.get_password('ctrlCctrlV.OpenRouter', 'api-key'))"), "from-rust");
    keyring("keyring.set_password('ctrlCctrlV.OpenRouter', 'api-key', 'from-python')");
    assert_eq!(credentials.read().unwrap(), "from-python");
    credentials.write("").unwrap();
    assert_eq!(keyring("print(keyring.get_password('ctrlCctrlV.OpenRouter', 'api-key'))"), "None");
    credentials.write(&previous).unwrap();
}
