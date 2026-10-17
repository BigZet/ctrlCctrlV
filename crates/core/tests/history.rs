use ctrlcctrlv_core::history::{EntryRef, History, NewRun};

fn open() -> (tempfile::TempDir, History) {
    let directory = tempfile::tempdir().unwrap();
    let history = History::open(directory.path()).unwrap();
    (directory, history)
}

#[test]
fn search_is_case_insensitive_for_cyrillic() {
    for query in ["папка", "ПАПКА", "КаРтОнНаЯ"] {
        let (directory, history) = open();
        let original = "Папка, картонная\r\nКоличество: 001\n";
        let id = history.add("ocr", original, "", "completed").unwrap().unwrap();
        history.add("ocr", "Ручка", "", "completed").unwrap();
        let found = History::open(directory.path()).unwrap().entries(query, 500).unwrap();
        assert_eq!(found.iter().map(|e| (e.id, e.text.as_str())).collect::<Vec<_>>(), [(id, original)]);
    }
}

#[test]
fn search_casefolds_unicode_and_keeps_query_literal() {
    let (_directory, history) = open();
    let id = history.add("ocr", "Straße_50%", "", "completed").unwrap().unwrap();
    history.add("ocr", "STRASSE A50B", "", "completed").unwrap();
    assert_eq!(history.entries("STRASSE_", 500).unwrap().iter().map(|e| e.id).collect::<Vec<_>>(), [id]);
    assert_eq!(history.entries("50%", 500).unwrap().iter().map(|e| e.id).collect::<Vec<_>>(), [id]);
    assert_eq!(history.get(id).unwrap().unwrap().text, "Straße_50%");
}

#[test]
fn empty_text_is_not_recorded_and_newest_comes_first() {
    let (_directory, history) = open();
    assert_eq!(history.add("ocr", "", "", "completed").unwrap(), None);
    let first = history.add("ocr", "one", "m", "completed").unwrap().unwrap();
    let second = history.add("typing", "two", "", "completed").unwrap().unwrap();
    let entries = history.entries("", 500).unwrap();
    assert_eq!(entries.iter().map(|e| e.id).collect::<Vec<_>>(), [second, first]);
    assert_eq!(history.entries("", 1).unwrap().len(), 1);
}

#[test]
fn delete_checks_identity_and_restore_keeps_or_replaces_id() {
    let (_directory, history) = open();
    let id = history.add("ocr", "text", "m", "completed").unwrap().unwrap();
    let entry = history.get(id).unwrap().unwrap();
    assert_eq!(history.delete(id, Some("1999-01-01T00:00:00+00:00")).unwrap(), None);
    let deleted = history.delete(id, Some(&entry.created)).unwrap().unwrap();
    assert_eq!(deleted, entry);
    assert!(history.get(id).unwrap().is_none());
    assert_eq!(history.restore(&deleted).unwrap(), entry);
    // If the ID was reused meanwhile, the entry comes back under a new one.
    history.delete(id, None).unwrap();
    let other = history.add("ocr", "other", "", "completed").unwrap().unwrap();
    assert_eq!(other, id);
    let restored = history.restore(&deleted).unwrap();
    assert_ne!(restored.id, id);
    assert_eq!(restored.text, "text");
}

#[test]
fn pins_come_first_and_stale_pins_are_ignored() {
    let (_directory, history) = open();
    let ids: Vec<i64> =
        (0..5).map(|i| history.add("ocr", &format!("text {i}"), "", "completed").unwrap().unwrap()).collect();
    let pinned = history.get(ids[1]).unwrap().unwrap().reference();
    let stale = EntryRef { id: ids[2], created: "1999-01-01T00:00:00+00:00".into() };
    let invalid = EntryRef { id: -1, created: String::new() };
    let visible = history.visible_entries(&[pinned.clone(), stale, invalid], "", 500).unwrap();
    assert_eq!(visible[0].id, ids[1]);
    assert_eq!(visible.len(), 5);
    assert_eq!(visible.iter().filter(|e| e.id == ids[1]).count(), 1);
    assert_eq!(
        history.visible_entries(std::slice::from_ref(&pinned), "", 1).unwrap().iter().map(|e| e.id).collect::<Vec<_>>(),
        [ids[1]]
    );
    assert!(history.visible_entries(std::slice::from_ref(&pinned), "", 0).unwrap().is_empty());
    assert!(history.visible_entries(&[pinned], "absent", 500).unwrap().is_empty());
}

#[test]
fn many_pins_are_queried_in_batches() {
    let (_directory, history) = open();
    let refs: Vec<EntryRef> = (0..1200)
        .map(|i| {
            let id = history.add("ocr", &format!("t{i}"), "", "completed").unwrap().unwrap();
            history.get(id).unwrap().unwrap().reference()
        })
        .collect();
    assert_eq!(history.pinned_entries(&refs, "").unwrap().len(), 1200);
    assert_eq!(history.visible_entries(&refs, "", 9999).unwrap().len(), 500);
}

#[test]
fn several_model_runs_stay_with_their_capture() {
    let (_directory, history) = open();
    let id = history.add("ocr", "first", "lightonocr-3-0.8b", "completed").unwrap().unwrap();
    let entry = history.get(id).unwrap().unwrap().reference();
    for (model, text) in
        [("lightonocr-3-0.8b", "first"), ("qwen3-vl-8b", "second"), ("qwen/qwen3-vl-8b-instruct", "third")]
    {
        let run = NewRun { provider: "local", model, text, raw_text: text, status: "completed", elapsed_ms: Some(10) };
        history.add_run(&entry, run).unwrap().unwrap();
    }
    let runs = history.runs(&entry).unwrap();
    assert_eq!(runs.iter().map(|r| r.text.as_str()).collect::<Vec<_>>(), ["first", "second", "third"]);
    assert!(history.update_text(&entry, "second", "qwen3-vl-8b").unwrap());
    assert_eq!(history.get(id).unwrap().unwrap().text, "second");

    // Runs survive delete and undo, and are dropped by compaction otherwise.
    let deleted = history.delete(id, None).unwrap().unwrap();
    let blocker = history.add("ocr", "blocker", "", "completed").unwrap().unwrap();
    let restored = history.restore(&deleted).unwrap();
    assert_ne!(restored.id, id);
    assert_eq!(history.runs(&restored.reference()).unwrap().len(), 3);
    history.delete(restored.id, None).unwrap();
    history.compact().unwrap();
    assert!(history.runs(&restored.reference()).unwrap().is_empty());
    assert!(history.get(blocker).unwrap().is_some());

    let missing = EntryRef { id: 999, created: "x".into() };
    assert!(history.add_run(&missing, NewRun::default()).unwrap().is_none());
}
