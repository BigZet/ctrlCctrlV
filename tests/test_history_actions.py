"""Recovery is memory-only and references include the original creation time."""
from dataclasses import replace
import sqlite3
import weakref

import pytest

from ctrlcctrlv.history import Entry, History


def seed(history, identifier, created, text="Straße_50%\r\n😀\t001\n"):
    entry = Entry(identifier, created, "ocr", text, "vision", "completed")
    with history.connect() as db:
        db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", tuple(entry.__dict__.values()))
    return entry


def test_deleted_max_id_reused_then_restored_preserves_both_records(tmp_path):
    history = History(tmp_path)
    original = seed(history, 1, "2001-01-01T00:00:00+00:00")
    assert history.delete(1) == original
    new_id = history.add("typing", "new entry", "", "typed")
    assert new_id == 1
    new_entry = history.get(new_id)
    restored = history.restore(original)
    assert restored == replace(original, id=2)
    assert history.get(1) == new_entry
    assert history.entries() == [new_entry, restored]


def test_restore_uses_free_original_id_and_preserves_original_date_order(tmp_path):
    history = History(tmp_path)
    older = seed(history, 4, "2000-01-01", "old")
    newer = seed(history, 2, "2001-01-01", "new")
    tied = seed(history, 3, "2001-01-01", "tie")
    history.delete(4)
    assert history.restore(older) == older
    assert history.entries() == [tied, newer, older]


def test_guarded_delete_rejects_reused_identity_and_returns_committed_entry(tmp_path):
    history = History(tmp_path)
    current = seed(history, 1, "2002-01-01")
    assert history.delete(1, expected_created="2001-01-01") is None
    assert history.get(1) == current
    assert history.delete(1, expected_created=current.created) == current
    assert history.get(1) is None
    assert history.delete(1) is None


def test_pins_bypass_recent_500_match_exact_identity_and_literal_unicode_search(tmp_path):
    from ctrlcctrlv.history import EntryRef
    history = History(tmp_path)
    old = seed(history, 1, "2000-01-01")
    with history.connect() as db:
        db.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?)", [
            (i, "2026-01-01", "ocr", "STRASSE_50% recent", "", "completed")
            for i in range(2, 503)
        ])
    refs = [EntryRef(1, old.created), EntryRef(502, "wrong"), EntryRef(1, old.created)]
    assert old not in history.entries()
    assert history.pinned_entries(refs, "STRASSE_50%") == [old]
    visible = history.visible_entries(refs, "strasse_50%")
    assert len(visible) == 500
    assert visible[0] == old
    assert len({(entry.id, entry.created) for entry in visible}) == 500
    assert history.visible_entries(refs, "not present") == []
    assert history.visible_entries(refs, limit=600) == visible
    assert history.visible_entries(refs, limit=0) == []


def test_pin_and_normal_groups_filter_and_do_not_duplicate(tmp_path):
    from ctrlcctrlv.history import EntryRef
    history = History(tmp_path)
    older = seed(history, 1, "2000", "ПАПКА")
    newer = seed(history, 2, "2001", "папка новая")
    seed(history, 3, "2002", "Ручка")
    refs = [EntryRef(older.id, older.created), EntryRef(newer.id, newer.created)]
    assert history.visible_entries(refs, "ПаПкА") == [newer, older]
    assert history.visible_entries(refs, "новая") == [newer]


def test_nonexistent_large_pin_id_is_stale_instead_of_breaking_history(tmp_path):
    from ctrlcctrlv.history import EntryRef
    history = History(tmp_path)
    entry = seed(history, 1, "2000")
    assert history.pinned_entries([EntryRef(2 ** 100, "2000")]) == []
    assert history.visible_entries([EntryRef(2 ** 100, "2000")]) == [entry]


def test_schema_and_secure_deletion_are_unchanged_and_compaction_is_separate(tmp_path):
    history = History(tmp_path)
    with history.connect() as db:
        before = db.execute("PRAGMA table_info(entries)").fetchall()
        assert before == [
            (0, "id", "INTEGER", 0, None, 1), (1, "created", "TEXT", 1, None, 0),
            (2, "kind", "TEXT", 1, None, 0), (3, "text", "TEXT", 1, None, 0),
            (4, "model", "TEXT", 1, None, 0), (5, "status", "TEXT", 1, None, 0),
        ]
        assert db.execute("PRAGMA secure_delete").fetchone() == (1,)
    entry = seed(history, 1, "2000", "secret_deleted_7654321")
    assert history.delete(1) == entry
    assert b"secret_deleted_7654321" not in history.path.read_bytes()
    history.restore(entry)
    history.compact()
    with history.connect() as db:
        assert db.execute("PRAGMA table_info(entries)").fetchall() == before
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("entries",)]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["history.sqlite3"]


def test_database_failure_does_not_partially_delete_or_restore(tmp_path):
    history = History(tmp_path)
    entry = seed(history, 1, "2000")
    with history.connect() as db:
        db.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT, 'locked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        history.delete(1)
    assert history.get(1) == entry
    with history.connect() as db:
        db.execute("DROP TRIGGER refuse_delete")
    history.delete(1)
    with history.connect() as db:
        db.execute("CREATE TRIGGER refuse_restore BEFORE INSERT ON entries BEGIN SELECT RAISE(ABORT, 'locked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        history.restore(entry)
    assert history.entries() == []


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_failed_commit_rolls_back_entire_history_action(tmp_path, monkeypatch, action):
    import ctrlcctrlv.history as module
    history = History(tmp_path)
    entry = seed(history, 1, "2000")
    if action == "restore":
        history.delete(1)
    # A real concurrent reader blocks the write transaction's commit. Lower
    # only its busy timeout to keep this failure-path test quick.
    real_connect = sqlite3.connect
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args, **kwargs:
                        real_connect(*args, **{**kwargs, "timeout": 0.01}))
    reader = real_connect(history.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM entries").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            if action == "delete":
                history.delete(1)
            else:
                history.restore(entry)
    finally:
        reader.close()
    assert history.get(1) == (entry if action == "delete" else None)


def test_compaction_failure_keeps_committed_delete_and_pending_undo(tmp_path, monkeypatch):
    import ctrlcctrlv.history as module
    from ctrlcctrlv.history_actions import HistoryUndo
    history = History(tmp_path)
    entry = seed(history, 1, "2000")
    undo = HistoryUndo()
    deleted = history.delete(1)
    undo.remember(deleted, True)
    real_connect = sqlite3.connect
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args, **kwargs:
                        real_connect(*args, **{**kwargs, "timeout": 0.01}))
    reader = real_connect(history.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM entries").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            history.compact()
    finally:
        reader.close()
    assert history.get(1) is None
    assert undo.peek() == (entry, True)
    assert history.restore(undo.peek()[0]) == entry
    undo.clear()
    assert undo.peek() is None


@pytest.mark.parametrize("elapsed", [30, 30.01])
def test_undo_expires_at_30_seconds_and_releases_deleted_text(elapsed):
    from ctrlcctrlv.history_actions import HistoryUndo
    clock = [100.0]
    undo = HistoryUndo(clock=lambda: clock[0])
    entry = Entry(1, "2000", "ocr", "sensitive", "", "completed")
    reference = weakref.ref(entry)
    undo.remember(entry, True)
    del entry
    clock[0] += elapsed
    assert undo.peek() is None
    assert reference() is None


def test_undo_peek_is_repeatable_until_clear_and_only_latest_deletion_is_retained():
    from ctrlcctrlv.history_actions import HistoryUndo
    clock = [100.0]
    undo = HistoryUndo(clock=lambda: clock[0])
    first = Entry(1, "2000", "ocr", "first", "", "completed")
    second = Entry(2, "2001", "typing", "second", "", "typed")
    first_ref, second_ref = weakref.ref(first), weakref.ref(second)
    undo.remember(first, True)
    del first
    clock[0] = 129.99
    assert undo.peek()[1] is True
    undo.remember(second, False)
    del second
    assert first_ref() is None
    assert undo.peek() == undo.peek() == (second_ref(), False)
    clock[0] = 130.01
    assert undo.peek() is not None
    undo.clear()
    assert undo.peek() is None
    assert second_ref() is None
