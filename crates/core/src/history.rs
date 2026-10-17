//! Explicit OCR/typing history; never monitors clipboard or diagnostic logs.
//!
//! The database is `history.sqlite3` with the Python app's schema, so either
//! version can open the other's history. Several model runs per capture
//! (for iterative re-checks and side-by-side comparison) live in an extra
//! `runs` table that the Python app ignores.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use rusqlite::functions::FunctionFlags;
use rusqlite::{Connection, OptionalExtension, Row, TransactionBehavior, params};

use crate::error::{Error, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub id: i64,
    /// UTC ISO 8601 timestamp, as written by Python's `datetime.isoformat()`.
    pub created: String,
    pub kind: String,
    pub text: String,
    pub model: String,
    pub status: String,
}

impl Entry {
    pub fn reference(&self) -> EntryRef {
        EntryRef { id: self.id, created: self.created.clone() }
    }
}

/// An ID alone can refer to a different entry after SQLite reuses it.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct EntryRef {
    pub id: i64,
    pub created: String,
}

pub const MAX_VISIBLE: usize = 500;

const COLUMNS: &str = "id,created,kind,text,model,status";

pub struct History {
    path: PathBuf,
    db: Mutex<Connection>,
}

fn storage(error: rusqlite::Error) -> Error {
    Error::operation(format!("Ошибка истории: {error}"))
}

fn entry(row: &Row<'_>) -> rusqlite::Result<Entry> {
    Ok(Entry {
        id: row.get(0)?,
        created: row.get(1)?,
        kind: row.get(2)?,
        text: row.get(3)?,
        model: row.get(4)?,
        status: row.get(5)?,
    })
}

/// Full Unicode case folding, like Python's `str.casefold` (ß matches SS).
pub fn casefold(text: &str) -> String {
    caseless::default_case_fold_str(text)
}

/// Python's `datetime.now(timezone.utc).isoformat()`: microseconds are
/// omitted when zero, so stored strings sort the same in both versions.
fn now() -> String {
    let now = chrono::Utc::now();
    if now.timestamp_subsec_micros() == 0 {
        now.format("%Y-%m-%dT%H:%M:%S+00:00").to_string()
    } else {
        now.format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string()
    }
}

impl History {
    pub fn open(directory: &Path) -> Result<Self> {
        let path = directory.join("history.sqlite3");
        let db = Connection::open(&path).map_err(storage)?;
        db.busy_timeout(Duration::from_secs(5)).map_err(storage)?;
        db.create_scalar_function(
            "unicode_casefold",
            1,
            FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC,
            |context| Ok(casefold(&context.get::<String>(0)?)),
        )
        .map_err(storage)?;
        db.pragma_update(None, "secure_delete", "ON").map_err(storage)?;
        db.execute(
            "CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL)",
            [],
        )
        .map_err(storage)?;
        db.execute_batch(
            "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL, entry_created TEXT NOT NULL, created TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, text TEXT NOT NULL, raw_text TEXT NOT NULL, status TEXT NOT NULL, elapsed_ms INTEGER);
             CREATE INDEX IF NOT EXISTS runs_entry ON runs (entry_id, entry_created);",
        )
        .map_err(storage)?;
        Ok(Self { path, db: Mutex::new(db) })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn with<T>(&self, action: impl FnOnce(&mut Connection) -> rusqlite::Result<T>) -> Result<T> {
        let mut db = self.db.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        action(&mut db).map_err(storage)
    }

    /// Stores a result; empty text is not recorded.
    pub fn add(&self, kind: &str, text: &str, model: &str, status: &str) -> Result<Option<i64>> {
        if text.is_empty() {
            return Ok(None);
        }
        self.with(|db| {
            db.execute(
                "INSERT INTO entries (created,kind,text,model,status) VALUES (?,?,?,?,?)",
                params![now(), kind, text, model, status],
            )?;
            Ok(Some(db.last_insert_rowid()))
        })
    }

    /// Newest first; `search` is a literal, case-insensitive substring.
    pub fn entries(&self, search: &str, limit: usize) -> Result<Vec<Entry>> {
        self.with(|db| {
            let mut statement = db.prepare_cached(&format!(
                "SELECT {COLUMNS} FROM entries WHERE instr(unicode_casefold(text), ?) > 0 ORDER BY created DESC, id DESC LIMIT ?"
            ))?;
            statement.query_map(params![casefold(search), limit as i64], entry)?.collect()
        })
    }

    /// Pinned entries that still exist with the same creation time, newest first.
    pub fn pinned_entries(&self, refs: &[EntryRef], search: &str) -> Result<Vec<Entry>> {
        let identities: std::collections::HashSet<&EntryRef> = refs.iter().filter(|r| r.id > 0).collect();
        if identities.is_empty() {
            return Ok(Vec::new());
        }
        let mut identifiers: Vec<i64> = identities.iter().map(|r| r.id).collect();
        identifiers.sort_unstable();
        identifiers.dedup();
        let search = casefold(search);
        let mut found = self.with(|db| {
            let mut found = Vec::new();
            // Bound placeholders even for callers beyond the UI's pin limit.
            for batch in identifiers.chunks(500) {
                let placeholders = vec!["?"; batch.len()].join(",");
                let mut statement = db.prepare(&format!(
                    "SELECT {COLUMNS} FROM entries WHERE id IN ({placeholders}) AND instr(unicode_casefold(text), ?) > 0"
                ))?;
                let mut values: Vec<&dyn rusqlite::ToSql> = batch.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
                values.push(&search);
                for row in statement.query_map(values.as_slice(), entry)? {
                    found.push(row?);
                }
            }
            Ok(found)
        })?;
        found.retain(|entry| identities.contains(&entry.reference()));
        found.sort_by(|a, b| (&b.created, b.id).cmp(&(&a.created, a.id)));
        Ok(found)
    }

    /// Matching pins first, then recent entries, at most [`MAX_VISIBLE`] in total.
    pub fn visible_entries(&self, refs: &[EntryRef], search: &str, limit: usize) -> Result<Vec<Entry>> {
        let limit = limit.min(MAX_VISIBLE);
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut pinned = self.pinned_entries(refs, search)?;
        pinned.truncate(limit);
        if pinned.len() == limit {
            return Ok(pinned);
        }
        let shown: std::collections::HashSet<EntryRef> = pinned.iter().map(Entry::reference).collect();
        let ordinary = self.entries(search, limit + pinned.len())?;
        pinned.extend(ordinary.into_iter().filter(|entry| !shown.contains(&entry.reference())));
        pinned.truncate(limit);
        Ok(pinned)
    }

    /// Deletes and returns the entry, unless it was replaced since `expected_created`.
    pub fn delete(&self, id: i64, expected_created: Option<&str>) -> Result<Option<Entry>> {
        self.with(|db| {
            // Lock before reading so the returned row is the row actually deleted.
            let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let row =
                transaction.query_row(&format!("SELECT {COLUMNS} FROM entries WHERE id=?"), [id], entry).optional()?;
            let Some(row) = row.filter(|row| expected_created.is_none_or(|created| row.created == created)) else {
                return Ok(None);
            };
            transaction.execute("DELETE FROM entries WHERE id=?", [id])?;
            transaction.commit()?;
            Ok(Some(row))
        })
    }

    /// Optional maintenance; failure does not invalidate a committed deletion.
    /// Also drops runs whose entry no longer exists (deleted without undo,
    /// or deleted by the Python app).
    pub fn compact(&self) -> Result<()> {
        self.with(|db| {
            db.execute(
                "DELETE FROM runs WHERE NOT EXISTS (SELECT 1 FROM entries WHERE entries.id = runs.entry_id AND entries.created = runs.entry_created)",
                [],
            )?;
            db.execute_batch("VACUUM")
        })
    }

    /// Undo for [`History::delete`]: keeps the ID when it is still free.
    pub fn restore(&self, entry: &Entry) -> Result<Entry> {
        self.with(|db| {
            let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let occupied =
                transaction.query_row("SELECT 1 FROM entries WHERE id=?", [entry.id], |_| Ok(())).optional()?.is_some();
            let values = params![entry.created, entry.kind, entry.text, entry.model, entry.status];
            if occupied {
                transaction
                    .execute("INSERT INTO entries (created,kind,text,model,status) VALUES (?,?,?,?,?)", values)?;
            } else {
                transaction.execute(
                    "INSERT INTO entries (id,created,kind,text,model,status) VALUES (?,?,?,?,?,?)",
                    params![entry.id, entry.created, entry.kind, entry.text, entry.model, entry.status],
                )?;
            }
            let id = transaction.last_insert_rowid();
            // Runs stay attached across delete and undo, even under a new ID.
            transaction.execute(
                "UPDATE runs SET entry_id=? WHERE entry_id=? AND entry_created=?",
                params![id, entry.id, entry.created],
            )?;
            transaction.commit()?;
            Ok(Entry { id, ..entry.clone() })
        })
    }

    pub fn get(&self, id: i64) -> Result<Option<Entry>> {
        self.with(|db| db.query_row(&format!("SELECT {COLUMNS} FROM entries WHERE id=?"), [id], entry).optional())
    }
}

/// One model's answer for a capture.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    pub id: i64,
    pub entry: EntryRef,
    pub created: String,
    /// `local` or `openrouter`.
    pub provider: String,
    pub model: String,
    pub text: String,
    pub raw_text: String,
    pub status: String,
    pub elapsed_ms: Option<i64>,
}

#[derive(Debug, Clone, Default)]
pub struct NewRun<'a> {
    pub provider: &'a str,
    pub model: &'a str,
    pub text: &'a str,
    pub raw_text: &'a str,
    pub status: &'a str,
    pub elapsed_ms: Option<i64>,
}

fn run(row: &Row<'_>) -> rusqlite::Result<Run> {
    Ok(Run {
        id: row.get(0)?,
        entry: EntryRef { id: row.get(1)?, created: row.get(2)? },
        created: row.get(3)?,
        provider: row.get(4)?,
        model: row.get(5)?,
        text: row.get(6)?,
        raw_text: row.get(7)?,
        status: row.get(8)?,
        elapsed_ms: row.get(9)?,
    })
}

const RUN_COLUMNS: &str = "id,entry_id,entry_created,created,provider,model,text,raw_text,status,elapsed_ms";

impl History {
    /// Records another model's answer for an existing entry.
    pub fn add_run(&self, entry: &EntryRef, new: NewRun<'_>) -> Result<Option<Run>> {
        self.with(|db| {
            let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let exists = transaction
                .query_row("SELECT 1 FROM entries WHERE id=? AND created=?", params![entry.id, entry.created], |_| Ok(()))
                .optional()?
                .is_some();
            if !exists {
                return Ok(None);
            }
            transaction.execute(
                "INSERT INTO runs (entry_id,entry_created,created,provider,model,text,raw_text,status,elapsed_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                params![entry.id, entry.created, now(), new.provider, new.model, new.text, new.raw_text, new.status, new.elapsed_ms],
            )?;
            let id = transaction.last_insert_rowid();
            let stored = transaction.query_row(&format!("SELECT {RUN_COLUMNS} FROM runs WHERE id=?"), [id], run)?;
            transaction.commit()?;
            Ok(Some(stored))
        })
    }

    /// All runs of an entry, oldest first.
    pub fn runs(&self, entry: &EntryRef) -> Result<Vec<Run>> {
        self.with(|db| {
            let mut statement = db.prepare_cached(&format!(
                "SELECT {RUN_COLUMNS} FROM runs WHERE entry_id=? AND entry_created=? ORDER BY created, id"
            ))?;
            statement.query_map(params![entry.id, entry.created], run)?.collect()
        })
    }

    /// Makes `text` (for example a chosen run) the entry's published text.
    pub fn update_text(&self, entry: &EntryRef, text: &str, model: &str) -> Result<bool> {
        self.with(|db| {
            let changed = db.execute(
                "UPDATE entries SET text=?, model=? WHERE id=? AND created=?",
                params![text, model, entry.id, entry.created],
            )?;
            Ok(changed == 1)
        })
    }
}
