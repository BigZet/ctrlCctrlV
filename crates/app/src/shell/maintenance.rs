//! Idle unloading reserves the same activity as foreground actions: the runtime
//! mutex alone is not a lease for a streamed request.
use super::{Activity, Shell, changed, lock, report, show_main};
use crate::model::{Phase, Status};
use ctrlcctrlv_core::settings::data_directory;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

pub fn start(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let shell = app.state::<Shell>();
        loop {
            tokio::select! {
                _ = shell.shutdown.cancelled() => break,
                _ = tokio::time::sleep(Duration::from_secs(1)) => {},
            }
            let activation = data_directory().join("activate.request");
            if activation.exists() && std::fs::remove_file(activation).is_ok() {
                show_main(&app, true);
            }
            let expired = lock(&shell.idle).expired(Instant::now(), shell.settings().idle_minutes);
            if !expired {
                continue;
            }
            let reserved = {
                let install = lock(&shell.install);
                install.is_none() && shell.begin(Activity::Maintenance)
            };
            if !reserved {
                continue;
            }
            let result = {
                let mut runtime = shell.runtime.lock().await;
                if runtime.is_ready() { Some(runtime.stop().await) } else { None }
            };
            shell.finish();
            if let Some(result) = result {
                report(
                    &app,
                    match result {
                        Ok(()) => Status::new(Phase::Idle, "Модель выгружена после простоя"),
                        Err(error) => Status::error(Phase::Idle, error.to_string()),
                    },
                );
                changed(&app);
            }
        }
    });
}
