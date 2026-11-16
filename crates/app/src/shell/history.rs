//! The history page: search, pins (in `ui-state.json`, as in the Python app),
//! delete with undo, and opening an entry with all its model runs.
use ctrlcctrlv_core::history::{EntryRef, MAX_VISIBLE};
use serde::Serialize;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

static NEXT_DELETION: AtomicU64 = AtomicU64::new(1);
const UNDO_LIFETIME: Duration = Duration::from_secs(30);

use super::{Capture, Shell, lock};
use crate::model::{RunKind, RunState, RunView, SessionView, events};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EntryView {
    id: i64,
    created: String,
    kind: String,
    text: String,
    model: String,
    status: String,
    pinned: bool,
    runs: usize,
}

fn history(shell: &Shell) -> Result<&ctrlcctrlv_core::history::History, String> {
    shell.history.as_ref().ok_or_else(|| "История недоступна".to_string())
}

#[tauri::command]
pub fn history_list(app: AppHandle, search: String) -> Result<Vec<EntryView>, String> {
    let shell = app.state::<Shell>();
    let history = history(&shell)?;
    let pins = lock(&shell.ui).history_pins();
    let entries = history.visible_entries(&pins, &search, MAX_VISIBLE).map_err(|e| e.to_string())?;
    Ok(entries
        .into_iter()
        .map(|entry| {
            let reference = entry.reference();
            let runs = if entry.kind == "ocr" { history.runs(&reference).map(|r| r.len()).unwrap_or(0) } else { 0 };
            EntryView {
                pinned: pins.contains(&reference),
                id: entry.id,
                created: entry.created,
                kind: entry.kind,
                text: entry.text,
                model: entry.model,
                status: entry.status,
                runs,
            }
        })
        .collect())
}

/// Shows an entry in the result window with its stored runs. The image is
/// never stored, so such a session can be edited and compared, not re-checked.
#[tauri::command]
pub fn history_open(app: AppHandle, id: i64, created: String) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let history = history(&shell)?;
    let entry = history.get(id).map_err(|e| e.to_string())?.filter(|e| e.created == created).ok_or("Запись удалена")?;
    let reference = entry.reference();
    let capture = super::ocr::next_capture();
    let mut runs: Vec<RunView> = history
        .runs(&reference)
        .unwrap_or_default()
        .into_iter()
        .enumerate()
        .map(|(index, run)| RunView {
            id: super::ocr::next_run(),
            capture,
            round: index as u32 + 1,
            kind: if index == 0 { RunKind::Ocr } else { RunKind::Review },
            label: shell.label(&run.provider, &run.model),
            provider: run.provider,
            model: run.model,
            state: RunState::Done,
            text: run.text,
            base: None,
            error: None,
            elapsed_ms: run.elapsed_ms.unwrap_or(0).max(0) as u64,
            format: "text".into(),
            warning: None,
        })
        .collect();
    let rounds = runs.len() as u32;
    // The entry's own text is what the user settled on; show it last.
    if runs.last().is_none_or(|run| run.text != entry.text) {
        runs.push(RunView {
            id: super::ocr::next_run(),
            capture,
            round: rounds + 1,
            kind: RunKind::Ocr,
            provider: "history".into(),
            model: entry.model.clone(),
            label: "Сохранённый текст".into(),
            state: RunState::Done,
            text: entry.text.clone(),
            base: None,
            error: None,
            elapsed_ms: 0,
            format: "text".into(),
            warning: None,
        });
    }
    let session = Capture {
        id: capture,
        png: None,
        entry: Some(reference),
        runs,
        rounds: rounds + 1,
        text: Some(entry.text.clone()),
    };
    let view: SessionView = session.view();
    *lock(&shell.capture) = Some(session);
    let _ = app.emit(events::SESSION, view);
    Ok(())
}

#[tauri::command]
pub fn history_pin(app: AppHandle, id: i64, created: String, pinned: bool) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let mut ui = lock(&shell.ui);
    let reference = EntryRef { id, created };
    let mut pins = ui.history_pins();
    pins.retain(|pin| *pin != reference);
    if pinned {
        pins.insert(0, reference);
    }
    ui.set_history_pins(&pins).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn history_delete(app: AppHandle, id: i64, created: String) -> Result<u64, String> {
    let shell = app.state::<Shell>();
    let history = history(&shell)?;
    let Some(deleted) = history.delete_with_runs(id, Some(&created)).map_err(|e| e.to_string())? else {
        return Err("Запись уже удалена".into());
    };
    let reference = deleted.entry.reference();
    let mut ui = lock(&shell.ui);
    let mut pins = ui.history_pins();
    let pinned = pins.contains(&reference);
    if pinned {
        pins.retain(|pin| *pin != reference);
        let _ = ui.set_history_pins(&pins);
    }
    let token = NEXT_DELETION.fetch_add(1, Ordering::Relaxed);
    *lock(&shell.deleted) = Some((deleted, pinned, Instant::now(), token));
    drop(ui);
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(UNDO_LIFETIME).await;
        let shell = app.state::<Shell>();
        let mut pending = lock(&shell.deleted);
        if pending.as_ref().is_some_and(|(_, _, _, id)| *id == token) {
            *pending = None;
        }
    });
    Ok(token)
}

#[tauri::command]
pub fn history_undo(app: AppHandle, deletion: u64) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let history = history(&shell)?;
    let mut pending = lock(&shell.deleted);
    if pending.as_ref().is_some_and(|(_, _, at, _)| at.elapsed() >= UNDO_LIFETIME) {
        *pending = None;
    }
    let Some((deleted, pinned, _, token)) = pending.as_ref() else {
        return Err("Нечего восстанавливать".into());
    };
    if *token != deletion {
        return Err("Это удаление уже недоступно для отмены".into());
    }
    let pinned = *pinned;
    let restored = history.restore_with_runs(deleted).map_err(|e| e.to_string())?;
    *pending = None;
    drop(pending);
    if pinned {
        let mut ui = lock(&shell.ui);
        let mut pins = ui.history_pins();
        pins.insert(0, restored.reference());
        ui.set_history_pins(&pins).map_err(|_| "Запись восстановлена, но закрепление не сохранено".to_string())?;
    }
    Ok(())
}
