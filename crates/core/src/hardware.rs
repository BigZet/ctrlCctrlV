//! Hardware capacity, read without loading a model or silently choosing CPU.

use std::path::Path;
use std::process::Stdio;
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde::Serialize;
use tokio_util::sync::CancellationToken;

use crate::catalog::{Acceleration, Model};
use crate::error::{Error, Result};

pub const MIB: u64 = 1024 * 1024;
pub const GIB: u64 = 1024 * MIB;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Device {
    pub backend: String,
    pub name: String,
    pub total_bytes: u64,
    pub available_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HardwareReport {
    pub system: String,
    pub architecture: String,
    pub physical_memory_bytes: Option<u64>,
    pub available_memory_bytes: Option<u64>,
    pub acceleration: Acceleration,
    pub devices: Vec<Device>,
}

/// Explicit, conservative preparation estimate; actual allocations depend on
/// backend and context and are still checked by the engine.
pub fn estimate_memory(model: &Model, slots: u32) -> u64 {
    (model.size() as f64 * 1.25) as u64 + GIB * slots as u64
}

pub fn memory_capacity() -> (Option<u64>, Option<u64>) {
    let mut system = sysinfo::System::new();
    system.refresh_memory();
    let total = Some(system.total_memory()).filter(|value| *value > 0);
    let available = Some(system.available_memory()).filter(|value| *value > 0);
    (total, available.map(|value| value.min(total.unwrap_or(u64::MAX))))
}

static DEVICE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A\s*([\w-]+):\s*(.{1,120}?)\s*\((\d+) MiB,\s*(\d+) MiB free\)\s*\z").unwrap());

/// GPU lines of `llama-server --list-devices`.
pub fn parse_devices(output: &str) -> Vec<Device> {
    output
        .lines()
        .filter_map(|line| DEVICE.captures(line))
        .filter(|m| !m[1].to_uppercase().starts_with("CPU"))
        .map(|m| Device {
            backend: m[1].to_string(),
            name: m[2].trim().to_string(),
            total_bytes: m[3].parse::<u64>().unwrap_or(0) * MIB,
            available_bytes: m[4].parse::<u64>().unwrap_or(0) * MIB,
        })
        .collect()
}

pub async fn inspect_hardware(
    binary: Option<&Path>,
    acceleration: Acceleration,
    cancel: &CancellationToken,
) -> Result<HardwareReport> {
    let cancelled = || Error::Cancelled("Проверка оборудования отменена".into());
    if cancel.is_cancelled() {
        return Err(cancelled());
    }
    let (total, available) = memory_capacity();
    let mut report = HardwareReport {
        system: crate::runtime::system_name().into(),
        architecture: std::env::consts::ARCH.into(),
        physical_memory_bytes: total,
        available_memory_bytes: available,
        acceleration,
        devices: Vec::new(),
    };
    if acceleration == Acceleration::Gpu {
        let binary = binary.ok_or_else(|| Error::operation("Сначала загрузите модель в разделе «Модели»"))?;
        let mut command = tokio::process::Command::new(binary);
        command
            .arg("--list-devices")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(directory) = binary.parent() {
            command.current_dir(directory);
        }
        crate::runtime::hide_window(&mut command);
        let failed = || {
            Error::operation(
                "Не удалось проверить GPU. Проверьте драйвер и установку движка или выберите CPU / OpenRouter.",
            )
        };
        let output = tokio::select! {
            _ = cancel.cancelled() => return Err(cancelled()),
            output = tokio::time::timeout(Duration::from_secs(10), command.output()) => output.map_err(|_| failed())?.map_err(|_| failed())?,
        };
        if !output.status.success() {
            return Err(Error::operation(
                "Движок не обнаружил доступный GPU. Проверьте драйвер или выберите CPU / OpenRouter.",
            ));
        }
        let text = format!("{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        report.devices = parse_devices(&text);
        if report.devices.is_empty() {
            return Err(Error::operation(
                "GPU недоступен. На Windows нужен драйвер NVIDIA; можно явно выбрать CPU или OpenRouter.",
            ));
        }
    }
    if cancel.is_cancelled() {
        return Err(cancelled());
    }
    Ok(report)
}

/// Fails with [`Error::InsufficientMemory`] when the estimate does not fit.
pub fn require_capacity(report: &HardwareReport, model: &Model, slots: u32) -> Result<u64> {
    let needed = estimate_memory(model, slots);
    let available = if report.acceleration == Acceleration::Gpu {
        Some(report.devices.iter().map(|d| d.available_bytes).max().unwrap_or(0))
    } else {
        report.available_memory_bytes.or(report.physical_memory_bytes)
    };
    if let Some(available) = available.filter(|available| *available < needed) {
        let memory = if report.acceleration == Acceleration::Gpu {
            "памяти GPU"
        } else {
            "оперативной памяти"
        };
        return Err(Error::InsufficientMemory(format!(
            "Недостаточно {memory}: доступно {:.1} ГБ, предварительная оценка для модели {:.1} ГБ. Выберите меньшую модель, CPU или OpenRouter.",
            available as f64 / GIB as f64,
            needed as f64 / GIB as f64
        )));
    }
    Ok(needed)
}
