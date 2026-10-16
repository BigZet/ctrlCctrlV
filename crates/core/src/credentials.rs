//! The OpenRouter API key in the system credential store, under the same
//! service and user name as the Python app (`keyring` package), so an
//! existing key keeps working after the switch.

#[cfg(target_os = "macos")]
use keyring_core::api::CredentialStoreApi;
#[cfg(windows)]
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::error::{Error, Result};

pub const SERVICE: &str = "ctrlCctrlV.OpenRouter";
pub const USERNAME: &str = "api-key";

/// Where a secret lives; the system store in the app, memory in tests.
pub trait SecretStore: Send + Sync {
    fn get(&self) -> Result<Option<String>>;
    fn set(&self, secret: &str) -> Result<()>;
    fn delete(&self) -> Result<()>;
}

pub struct Credentials {
    store: Arc<dyn SecretStore>,
}

impl Credentials {
    pub fn new(store: Arc<dyn SecretStore>) -> Self {
        Self { store }
    }

    /// Windows Credential Manager or the macOS login keychain.
    pub fn system() -> Result<Self> {
        Ok(Self::new(Arc::new(SystemStore::new()?)))
    }

    /// The key, or an empty string when none is stored.
    pub fn read(&self) -> Result<String> {
        Ok(self.store.get()?.unwrap_or_default())
    }

    /// Stores the key; an empty value removes a stored key.
    pub fn write(&self, value: &str) -> Result<()> {
        if !value.is_empty() {
            self.store.set(value)
        } else if !self.read()?.is_empty() {
            self.store.delete()
        } else {
            Ok(())
        }
    }
}

#[derive(Default)]
pub struct MemoryStore(Mutex<Option<String>>);

impl SecretStore for MemoryStore {
    fn get(&self) -> Result<Option<String>> {
        Ok(self.0.lock().unwrap().clone())
    }

    fn set(&self, secret: &str) -> Result<()> {
        *self.0.lock().unwrap() = Some(secret.to_string());
        Ok(())
    }

    fn delete(&self) -> Result<()> {
        *self.0.lock().unwrap() = None;
        Ok(())
    }
}

fn store_error(error: keyring_core::Error) -> Error {
    Error::operation(format!("Хранилище ключей недоступно: {error}"))
}

pub struct SystemStore {
    #[cfg(windows)]
    store: std::sync::Arc<keyring_core::CredentialStore>,
    entry: keyring_core::Entry,
}

impl SystemStore {
    #[cfg(windows)]
    pub fn new() -> Result<Self> {
        let store: Arc<keyring_core::CredentialStore> =
            windows_native_keyring_store::Store::new().map_err(store_error)?;
        let entry = windows_target(&store, SERVICE)?;
        Ok(Self { store, entry })
    }

    #[cfg(target_os = "macos")]
    pub fn new() -> Result<Self> {
        // Generic password: service = SERVICE, account = USERNAME, as in Python.
        let store = apple_native_keyring_store::keychain::Store::new().map_err(store_error)?;
        let entry = store.build(SERVICE, USERNAME, None).map_err(store_error)?;
        Ok(Self { entry })
    }

    #[cfg(not(any(windows, target_os = "macos")))]
    pub fn new() -> Result<Self> {
        Err(Error::operation("Хранилище ключей поддерживается только в Windows и macOS"))
    }
}

fn read(entry: &keyring_core::Entry) -> Result<Option<String>> {
    match entry.get_password() {
        Ok(secret) => Ok(Some(secret)),
        Err(keyring_core::Error::NoEntry) => Ok(None),
        Err(error) => Err(store_error(error)),
    }
}

/// Python keyring on Windows names the credential after the service and keeps
/// the user name as an attribute; a credential owned by another user name is
/// kept under `user@service`.
#[cfg(windows)]
fn windows_target(store: &Arc<keyring_core::CredentialStore>, target: &str) -> Result<keyring_core::Entry> {
    store.build(SERVICE, USERNAME, Some(&HashMap::from([("target", target)]))).map_err(store_error)
}

#[cfg(windows)]
fn owner(entry: &keyring_core::Entry) -> Option<String> {
    entry.get_attributes().ok()?.get("username").cloned()
}

#[cfg(windows)]
fn save(entry: &keyring_core::Entry, username: &str, secret: &str) -> Result<()> {
    entry.set_password(secret).map_err(store_error)?;
    entry.update_attributes(&HashMap::from([("username", username)])).map_err(store_error)
}

#[cfg(windows)]
impl SecretStore for SystemStore {
    fn get(&self) -> Result<Option<String>> {
        if owner(&self.entry).as_deref() == Some(USERNAME) {
            return read(&self.entry);
        }
        read(&windows_target(&self.store, &format!("{USERNAME}@{SERVICE}"))?)
    }

    fn set(&self, secret: &str) -> Result<()> {
        if let (Some(existing), Some(previous)) = (owner(&self.entry), read(&self.entry)?)
            && existing != USERNAME
        {
            save(&windows_target(&self.store, &format!("{existing}@{SERVICE}"))?, &existing, &previous)?;
        }
        save(&self.entry, USERNAME, secret)
    }

    fn delete(&self) -> Result<()> {
        let compound = windows_target(&self.store, &format!("{USERNAME}@{SERVICE}"))?;
        for entry in [&self.entry, &compound] {
            if owner(entry).as_deref() == Some(USERNAME) {
                entry.delete_credential().map_err(store_error)?;
            }
        }
        Ok(())
    }
}

#[cfg(not(windows))]
impl SecretStore for SystemStore {
    fn get(&self) -> Result<Option<String>> {
        read(&self.entry)
    }

    fn set(&self, secret: &str) -> Result<()> {
        self.entry.set_password(secret).map_err(store_error)
    }

    fn delete(&self) -> Result<()> {
        match self.entry.delete_credential() {
            Ok(()) | Err(keyring_core::Error::NoEntry) => Ok(()),
            Err(error) => Err(store_error(error)),
        }
    }
}
