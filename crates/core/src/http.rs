//! HTTP clients shared by the whole app, so connections and TLS sessions are reused.

use std::sync::LazyLock;
use std::time::Duration;

/// One client for internet services (OpenRouter, downloads) that honours the
/// system proxy, and one for the loopback engine that never uses a proxy.
#[derive(Debug, Clone)]
pub struct Http {
    pub remote: reqwest::Client,
    pub local: reqwest::Client,
}

impl Http {
    pub fn new() -> Self {
        let builder = || {
            reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .read_timeout(Duration::from_secs(120))
                .pool_idle_timeout(Duration::from_secs(90))
                .user_agent(concat!("ctrlCctrlV/", env!("CARGO_PKG_VERSION")))
        };
        Self {
            remote: builder().build().expect("HTTP client"),
            local: builder().no_proxy().build().expect("HTTP client"),
        }
    }

    /// The process-wide clients.
    pub fn shared() -> &'static Http {
        static SHARED: LazyLock<Http> = LazyLock::new(Http::new);
        &SHARED
    }

    pub fn client(&self, local: bool) -> &reqwest::Client {
        if local { &self.local } else { &self.remote }
    }
}

impl Default for Http {
    fn default() -> Self {
        Self::new()
    }
}
