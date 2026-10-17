//! Screen capture of only the selected region, and fast PNG encoding.
use image::codecs::png::{CompressionType, FilterType, PngEncoder};
use image::{ImageEncoder, RgbaImage};

use crate::{Error, Result};

/// Selection in logical (DPI-independent) pixels relative to a monitor's
/// top-left corner, as the overlay window reports it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Region {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Convert a logical region to the monitor's native capture units, rounding
/// outward so no selected pixel is lost and clamping to the monitor.
/// `scale` is native units per logical pixel; `None` when nothing remains.
pub fn native_rect(region: Region, scale: f64, bounds: (u32, u32)) -> Option<(u32, u32, u32, u32)> {
    let clamp = |v: f64, max: u32| (v.max(0.0) as u32).min(max);
    let left = clamp((region.x * scale).floor(), bounds.0);
    let top = clamp((region.y * scale).floor(), bounds.1);
    let right = clamp(((region.x + region.width) * scale).ceil(), bounds.0);
    let bottom = clamp(((region.y + region.height) * scale).ceil(), bounds.1);
    (right > left && bottom > top).then(|| (left, top, right - left, bottom - top))
}

/// Encode as RGB PNG with fast compression. Screen captures are opaque, so the
/// alpha channel only costs encoding time and upload size.
pub fn encode_png(image: &RgbaImage) -> Result<Vec<u8>> {
    let rgb: Vec<u8> = image.as_raw().as_chunks::<4>().0.iter().flat_map(|p| [p[0], p[1], p[2]]).collect();
    let mut out = Vec::with_capacity(rgb.len() / 4);
    PngEncoder::new_with_quality(&mut out, CompressionType::Fast, FilterType::Adaptive)
        .write_image(&rgb, image.width(), image.height(), image::ExtendedColorType::Rgb8)
        .map_err(|e| Error::Operation(format!("Не удалось сохранить снимок: {e}")))?;
    Ok(out)
}

#[cfg(any(windows, target_os = "macos"))]
pub use native::*;

#[cfg(any(windows, target_os = "macos"))]
mod native {
    use super::{Region, native_rect};
    use crate::{Error, Result};
    use image::RgbaImage;
    use xcap::Monitor;

    #[derive(Debug, Clone, PartialEq)]
    pub struct MonitorInfo {
        pub id: u32,
        pub name: String,
        /// Position and size in the system's desktop coordinates: physical
        /// pixels on Windows, points on macOS.
        pub x: i32,
        pub y: i32,
        pub width: u32,
        pub height: u32,
        /// Image pixels per logical pixel.
        pub scale_factor: f32,
        pub primary: bool,
    }

    fn failed(e: xcap::XCapError) -> Error {
        Error::Operation(format!("Не удалось сделать снимок экрана: {e}"))
    }

    fn info(monitor: &Monitor) -> Result<MonitorInfo> {
        Ok(MonitorInfo {
            id: monitor.id().map_err(failed)?,
            name: monitor.friendly_name().or_else(|_| monitor.name()).unwrap_or_default(),
            x: monitor.x().map_err(failed)?,
            y: monitor.y().map_err(failed)?,
            width: monitor.width().map_err(failed)?,
            height: monitor.height().map_err(failed)?,
            scale_factor: monitor.scale_factor().map_err(failed)?,
            primary: monitor.is_primary().unwrap_or(false),
        })
    }

    pub fn monitors() -> Result<Vec<MonitorInfo>> {
        Monitor::all().map_err(failed)?.iter().map(info).collect()
    }

    /// Capture only `region` of the monitor `monitor_id`. The result is in
    /// physical pixels (2x on Retina).
    pub fn capture_region(monitor_id: u32, region: Region) -> Result<RgbaImage> {
        let monitor = Monitor::all()
            .map_err(failed)?
            .into_iter()
            .find(|m| m.id().ok() == Some(monitor_id))
            .ok_or_else(|| Error::Operation("Монитор отключён. Выделите область заново.".into()))?;
        let info = info(&monitor)?;
        // Windows desktop coordinates are physical pixels; macOS uses points.
        let scale = if cfg!(windows) { info.scale_factor as f64 } else { 1.0 };
        let (x, y, w, h) = native_rect(region, scale, (info.width, info.height))
            .ok_or_else(|| Error::Operation("Выделенная область пуста".into()))?;
        monitor.capture_region(x, y, w, h).map_err(failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_outward_and_clamps() {
        let r = Region { x: 10.4, y: 0.6, width: 20.2, height: 5.0 };
        assert_eq!(native_rect(r, 1.0, (100, 100)), Some((10, 0, 21, 6)));
        assert_eq!(native_rect(r, 1.5, (100, 100)), Some((15, 0, 31, 9)));
        let off = Region { x: -5.0, y: 90.0, width: 20.0, height: 50.0 };
        assert_eq!(native_rect(off, 1.0, (100, 100)), Some((0, 90, 15, 10)));
        assert_eq!(native_rect(Region { x: 3.0, y: 3.0, width: 0.0, height: 4.0 }, 1.0, (100, 100)), None);
    }

    #[test]
    fn png_round_trips_without_alpha() {
        let image = RgbaImage::from_fn(7, 3, |x, y| image::Rgba([x as u8 * 30, y as u8 * 80, 7, 255]));
        let png = encode_png(&image).unwrap();
        let decoded = image::load_from_memory(&png).unwrap();
        assert_eq!(decoded.color(), image::ColorType::Rgb8);
        assert_eq!(decoded.to_rgba8(), image);
    }
}
