"""Explicit OCR/typing history; never monitors clipboard or diagnostic logs."""
from dataclasses import dataclass, replace
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3


@dataclass(frozen=True)
class Entry:
    id: int
    created: str
    kind: str
    text: str
    model: str
    status: str


@dataclass(frozen=True)
class EntryRef:
    """An ID alone can refer to a different entry after SQLite reuses it."""
    id: int
    created: str


class History:
    def __init__(self, directory):
        self.path = directory / "history.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.create_function("unicode_casefold", 1, str.casefold, deterministic=True)
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    def add(self, kind, text, model="", status="completed"):
        if not text:
            return None
        with self.connect() as db:
            cursor = db.execute("INSERT INTO entries (created,kind,text,model,status) VALUES (?,?,?,?,?)",
                                (datetime.now(timezone.utc).isoformat(), kind, text, model, status))
            return cursor.lastrowid

    def entries(self, search="", limit=500):
        with self.connect() as db:
            rows = db.execute("SELECT id,created,kind,text,model,status FROM entries WHERE instr(unicode_casefold(text), ?) > 0 ORDER BY created DESC, id DESC LIMIT ?", (search.casefold(), limit)).fetchall()
        return [Entry(*row) for row in rows]

    def pinned_entries(self, refs: Sequence[EntryRef], search: str = "") -> list[Entry]:
        # UiState accepts positive Python integers; IDs above SQLite's range
        # cannot identify a row and are simply stale references.
        identities = {ref for ref in refs if 0 < ref.id <= 2 ** 63 - 1}
        if not identities:
            return []
        identifiers = list({ref.id for ref in identities})
        found = []
        with self.connect() as db:
            # Bound placeholders even for callers beyond UiState's 100-pin limit.
            for start in range(0, len(identifiers), 500):
                batch = identifiers[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(
                    "SELECT id,created,kind,text,model,status FROM entries "
                    f"WHERE id IN ({placeholders}) AND instr(unicode_casefold(text), ?) > 0",
                    (*batch, search.casefold()),
                ).fetchall()
                found.extend(Entry(*row) for row in rows if EntryRef(row[0], row[1]) in identities)
        return sorted(found, key=lambda entry: (entry.created, entry.id), reverse=True)

    def visible_entries(self, refs: Sequence[EntryRef], search: str = "", limit: int = 500) -> list[Entry]:
        """Show matching pins first, then recent entries, with a total cap of 500."""
        limit = min(500, max(0, limit))
        if not limit:
            return []
        pinned = self.pinned_entries(refs, search)[:limit]
        if len(pinned) == limit:
            return pinned
        identities = {EntryRef(entry.id, entry.created) for entry in pinned}
        ordinary = [entry for entry in self.entries(search, limit=limit + len(pinned))
                    if EntryRef(entry.id, entry.created) not in identities]
        return (pinned + ordinary)[:limit]

    def delete(self, identifier: int, *, expected_created: str | None = None) -> Entry | None:
        with self.connect() as db:
            # Lock before reading so the returned row is the row actually deleted.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,created,kind,text,model,status FROM entries WHERE id=?", (identifier,)).fetchone()
            if row is None or (expected_created is not None and row[1] != expected_created):
                return None
            db.execute("DELETE FROM entries WHERE id=?", (identifier,))
        # The context commits before callers can retain the entry for Undo.
        return Entry(*row)

    def compact(self) -> None:
        """Optional maintenance; failure does not invalidate a committed deletion."""
        with self.connect() as db:
            db.execute("VACUUM")

    def restore(self, entry: Entry) -> Entry:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            occupied = db.execute("SELECT 1 FROM entries WHERE id=?", (entry.id,)).fetchone()
            values = (entry.created, entry.kind, entry.text, entry.model, entry.status)
            if occupied:
                cursor = db.execute("INSERT INTO entries (created,kind,text,model,status) VALUES (?,?,?,?,?)", values)
            else:
                cursor = db.execute("INSERT INTO entries (id,created,kind,text,model,status) VALUES (?,?,?,?,?,?)", (entry.id, *values))
            restored = replace(entry, id=cursor.lastrowid)
        return restored

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT id,created,kind,text,model,status FROM entries WHERE id=?", (identifier,)).fetchone()
        return Entry(*row) if row else None
