fn main() {
    // The shell only exists on Windows and macOS; other hosts build a stub.
    let os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if os == "windows" || os == "macos" {
        tauri_build::build();
    }
}
