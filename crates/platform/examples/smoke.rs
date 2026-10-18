//! Manual check on a real machine:
//! `cargo run --release --example smoke -- [type]`
//! Prints permissions and monitors, captures a 400x200 region of the primary
//! monitor to `smoke.png` with timings, and with `type` types a sample into
//! the window focused three seconds later.
#[cfg(not(any(windows, target_os = "macos")))]
fn main() {}

#[cfg(any(windows, target_os = "macos"))]
mod native {
    use std::time::{Duration, Instant};

    use ctrlcctrlv_platform::capture::{Region, capture_region, encode_png, monitors};
    use ctrlcctrlv_platform::clipboard::Clipboard;
    use ctrlcctrlv_platform::typing::{CancelToken, TypingSettings, prepare, type_chars};
    use ctrlcctrlv_platform::{NativeKeyboard, permissions};

    pub fn main() -> Result<(), Box<dyn std::error::Error>> {
        println!("{:?}", permissions());
        let all = monitors()?;
        for monitor in &all {
            println!("{monitor:?}");
        }
        let primary = all.iter().find(|m| m.primary).unwrap_or(&all[0]);
        let started = Instant::now();
        let image = capture_region(primary.id, Region { x: 0.0, y: 0.0, width: 400.0, height: 200.0 })?;
        let captured = started.elapsed();
        let png = encode_png(&image)?;
        println!(
            "capture {:?}, png {:?}, {}x{}, {} bytes",
            captured,
            started.elapsed() - captured,
            image.width(),
            image.height(),
            png.len()
        );
        std::fs::write("smoke.png", &png)?;

        let mut clipboard = Clipboard::new()?;
        clipboard.set_text("ctrlCctrlV")?;
        assert_eq!(clipboard.text()?, "ctrlCctrlV");

        if std::env::args().any(|a| a == "type") {
            println!("Focus a text editor; typing starts in 3 s.");
            std::thread::sleep(Duration::from_secs(3));
            let text = "Привет, ctrlCctrlV 🙂\n\tif x:\n\t\treturn 1\n";
            let settings = TypingSettings { autoindent: true, spaces_per_tab: 4, ..Default::default() };
            let started = Instant::now();
            let chars = prepare(text, &settings);
            type_chars(&NativeKeyboard, &chars, 0, &settings, &CancelToken::new(), |_, _| {}, None).1?;
            println!("typed {} chars in {:?}", text.chars().count(), started.elapsed());
        }
        Ok(())
    }
}

#[cfg(any(windows, target_os = "macos"))]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    native::main()
}
