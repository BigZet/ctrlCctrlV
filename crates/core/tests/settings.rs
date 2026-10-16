use ctrlcctrlv_core::models::ModelRegistry;
use ctrlcctrlv_core::settings::Settings;

fn models() -> ModelRegistry {
    ModelRegistry::builtin()
}

#[test]
fn settings_roundtrip_without_key() {
    let directory = tempfile::tempdir().unwrap();
    let settings = Settings {
        remote_model: "another/vision".into(),
        autoindent: true,
        capture_hotkey: "ctrl+cmd+5".into(),
        show_preview: false,
        ..Settings::default()
    };
    settings.save(directory.path(), &models()).unwrap();
    assert_eq!(Settings::load(directory.path(), &models()), settings);
    assert!(!std::fs::read_to_string(directory.path().join("settings.json")).unwrap().contains("api-key"));
}

#[test]
fn duplicate_hotkeys_rejected() {
    let settings = Settings { typing_hotkey: Settings::default().capture_hotkey, ..Settings::default() };
    assert!(settings.validate(&models()).unwrap_err().to_string().contains("различаться"));
    // Aliases normalise to the same shortcut.
    let settings =
        Settings { capture_hotkey: "ctrl+win+4".into(), typing_hotkey: "CMD + ctrl + 4".into(), ..Settings::default() };
    assert!(settings.validate(&models()).unwrap_err().to_string().contains("различаться"));
}

#[test]
fn broken_configuration_recovers_defaults() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::write(directory.path().join("settings.json"), r#"{"provider":"wrong"}"#).unwrap();
    assert_eq!(Settings::load(directory.path(), &models()), Settings::default());
}

#[test]
fn older_settings_enable_preview_and_inherit_review_provider() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::write(directory.path().join("settings.json"), r#"{"provider":"openrouter"}"#).unwrap();
    let settings = Settings::load(directory.path(), &models());
    assert!(settings.show_preview);
    assert_eq!(settings.review_provider, "openrouter");
}

#[test]
fn non_object_deep_or_oversized_settings_recover_without_deleting_file() {
    let deep = format!("{}0{}", "[".repeat(3000), "]".repeat(3000));
    let oversized = " ".repeat(65537);
    for content in ["[]", "null", "true", "123", "\"text\"", &deep, &oversized] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("settings.json");
        std::fs::write(&path, content).unwrap();
        assert_eq!(
            Settings::load(directory.path(), &models()),
            Settings::default(),
            "{}",
            &content[..content.len().min(20)]
        );
        assert_eq!(std::fs::read_to_string(&path).unwrap(), content);
    }
}

#[test]
fn invalid_field_types_and_values_fall_back_to_defaults() {
    let cases = [
        ("capture_hotkey", "null"),
        ("cancel_hotkey", "[]"),
        ("provider", "{}"),
        ("local_model", "[]"),
        ("local_model", "\"removed-model\""),
        ("remote_model", "{}"),
        ("remote_model", "\" \""),
        ("remote_mode", "[]"),
        ("review_local_model", "[]"),
        ("review_remote_model", "[]"),
        ("show_preview", "1"),
        ("autoindent", "\"yes\""),
        ("idle_minutes", "1.5"),
        ("spaces_per_tab", "true"),
        ("max_tokens", "256.5"),
        ("max_tokens", "256.0"),
        ("character_delay", "\"0.01\""),
        ("newline_delay", "false"),
        ("character_delay", "NaN"),
        ("newline_delay", "Infinity"),
        ("review_local_model", "\"lightonocr-3-0.8b\""),
        ("max_tokens", "100000"),
        ("idle_minutes", "0"),
    ];
    for (name, value) in cases {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(directory.path().join("settings.json"), format!("{{\"{name}\": {value}}}")).unwrap();
        assert_eq!(Settings::load(directory.path(), &models()), Settings::default(), "{name}={value}");
    }
}

#[test]
fn integer_delays_are_accepted_like_python() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::write(directory.path().join("settings.json"), r#"{"newline_delay": 1, "max_tokens": 512}"#).unwrap();
    let settings = Settings::load(directory.path(), &models());
    assert_eq!(settings.newline_delay, 1.0);
    assert_eq!(settings.max_tokens, 512);
}

#[test]
fn oversized_save_preserves_existing_settings() {
    let directory = tempfile::tempdir().unwrap();
    Settings { show_preview: false, ..Settings::default() }.save(directory.path(), &models()).unwrap();
    let path = directory.path().join("settings.json");
    let previous = std::fs::read(&path).unwrap();
    let error = Settings { remote_model: "x".repeat(65536), ..Settings::default() }
        .save(directory.path(), &models())
        .unwrap_err();
    assert!(error.to_string().contains("большие"));
    assert_eq!(std::fs::read(&path).unwrap(), previous);
}

#[test]
fn unknown_keys_from_newer_versions_survive_a_save() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::write(directory.path().join("settings.json"), r#"{"future_option": {"a": 1}, "autoindent": true}"#)
        .unwrap();
    let settings = Settings::load(directory.path(), &models());
    assert!(settings.autoindent);
    settings.save(directory.path(), &models()).unwrap();
    let saved: serde_json::Value =
        serde_json::from_slice(&std::fs::read(directory.path().join("settings.json")).unwrap()).unwrap();
    assert_eq!(saved["future_option"], serde_json::json!({"a": 1}));
}

#[test]
fn custom_models_are_valid_settings_choices() {
    let directory = tempfile::tempdir().unwrap();
    let custom = serde_json::json!([{
        "id": "custom-0123456789abcdef", "label": "Custom", "source": "https://huggingface.co/a/b",
        "license": "MIT", "mode": "json",
        "files": [
            {"filename": "model.gguf", "url": "https://huggingface.co/a/b/resolve/x/m.gguf", "sha256": "a".repeat(64), "size": 10},
            {"filename": "mmproj.gguf", "url": "https://huggingface.co/a/b/resolve/x/p.gguf", "sha256": "b".repeat(64), "size": 10}
        ]
    }]);
    std::fs::write(directory.path().join("custom-models.json"), custom.to_string()).unwrap();
    let registry = ModelRegistry::load(directory.path());
    assert!(registry.errors().is_empty());
    let settings = Settings { local_model: "custom-0123456789abcdef".into(), ..Settings::default() };
    settings.validate(&registry).unwrap();
    assert!(settings.validate(&models()).is_err());
}

#[test]
fn unsafe_custom_models_are_reported_and_ignored() {
    let directory = tempfile::tempdir().unwrap();
    let custom = serde_json::json!([{
        "id": "custom-0123456789abcdef", "label": "Custom", "source": "s",
        "files": [
            {"filename": "model.gguf", "url": "http://example.com/m.gguf", "sha256": "a".repeat(64), "size": 10},
            {"filename": "mmproj.gguf", "url": "https://example.com/p.gguf", "sha256": "b".repeat(64), "size": 10}
        ]
    }]);
    std::fs::write(directory.path().join("custom-models.json"), custom.to_string()).unwrap();
    let registry = ModelRegistry::load(directory.path());
    assert_eq!(registry.errors().len(), 1);
    assert!(!registry.contains("custom-0123456789abcdef"));
    assert!(registry.contains("qwen3-vl-4b"));
}
