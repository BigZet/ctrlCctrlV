from ctrlcctrlv.history import History
from ctrlcctrlv.history_actions import HistoryUndo


def test_delete_clears_rust_runs_and_retryable_undo_restores_under_new_id(tmp_path):
    history = History(tmp_path)
    with history.connect() as db:
        db.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, entry_id INTEGER, entry_created TEXT, created TEXT, provider TEXT, model TEXT, text TEXT, raw_text TEXT, status TEXT, elapsed_ms INTEGER)"
        )
    identifier = history.add("ocr", "published")
    entry = history.get(identifier)
    with history.connect() as db:
        db.execute(
            "INSERT INTO runs VALUES (1,?,?,?,?,?,?,?,?,?)",
            (
                entry.id,
                entry.created,
                entry.created,
                "local",
                "m",
                "answer",
                "raw answer",
                "completed",
                10,
            ),
        )
    deleted, runs = history.delete_with_runs(identifier)
    with history.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    blocker = history.add("ocr", "other")
    undo = HistoryUndo()
    undo.remember(deleted, True, runs)
    restored = history.restore(undo.peek()[0], undo.runs)
    assert restored.id != blocker
    with history.connect() as db:
        assert db.execute("SELECT entry_id,text,raw_text FROM runs").fetchone() == (
            restored.id,
            "answer",
            "raw answer",
        )
    undo.clear()
    assert not undo.runs


def test_startup_cleans_legacy_orphan_runs(tmp_path):
    history = History(tmp_path)
    with history.connect() as db:
        db.execute(
            "CREATE TABLE runs (entry_id INTEGER, entry_created TEXT, text TEXT)"
        )
        db.execute("INSERT INTO runs VALUES (999,'gone','private')")
    History(tmp_path)
    with history.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
