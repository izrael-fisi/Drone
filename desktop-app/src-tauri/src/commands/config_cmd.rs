use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose, Engine as _};
use serde::Serialize;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::UNIX_EPOCH;
use zip::ZipArchive;

const LOG_PREVIEW_LIMIT: usize = 5;
const IMAGE_PREVIEW_LIMIT: usize = 4;
const IMAGE_PREVIEW_MAX_BYTES: u64 = 1_500_000;

#[derive(Serialize)]
pub struct SupportBundleSummary {
    pub bundle_id: Option<String>,
    pub bundle_health_status: Option<String>,
    pub checksum_status: Option<String>,
    pub covered_file_count: Option<u64>,
    pub elevation_status: Option<String>,
    pub elevation_asset_count: Option<u64>,
    pub vertical_sanity_ready: Option<bool>,
    pub map_source: Option<String>,
    pub source_name: Option<String>,
    pub georef_source: Option<String>,
    pub georef_crs: Option<String>,
    pub georef_confidence: Option<f64>,
    pub replay_gate_status: Option<String>,
    pub replay_case_count: Option<u64>,
    pub px4_sitl_evidence_status: Option<String>,
    pub px4_sitl_sample_count: Option<u64>,
    pub px4_params_status: Option<String>,
    pub px4_ev_ctrl: Option<i64>,
    pub bench_readiness_status: Option<String>,
    pub bench_readiness_failed_count: Option<u64>,
    pub bench_readiness_degraded_count: Option<u64>,
}

#[derive(Serialize)]
pub struct SupportBundleFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub summary: Option<SupportBundleSummary>,
}

#[derive(Serialize)]
pub struct SupportBundleLogSummary {
    pub name: String,
    pub total_records: Option<u64>,
    pub accepted_rate: Option<f64>,
    pub status_counts: Option<serde_json::Value>,
    pub reason_counts: Option<serde_json::Value>,
    pub external_position: Option<serde_json::Value>,
}

#[derive(Serialize)]
pub struct SupportBundleReplayReport {
    pub case_name: Option<String>,
    pub expected: Option<String>,
    pub status: Option<String>,
    pub accepted_rate: Option<f64>,
    pub total_records: Option<u64>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundlePx4EvidenceReport {
    pub status: Option<String>,
    pub expected_message: Option<String>,
    pub sample_count: Option<u64>,
    pub latest_sample_age_s: Option<f64>,
    pub last_position: Option<serde_json::Value>,
    pub mavlink_version: Option<u64>,
    pub has_udp_14550: Option<bool>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundlePx4ParamReport {
    pub status: Option<String>,
    pub ev_ctrl: Option<i64>,
    pub hgt_ref: Option<i64>,
    pub gps_ctrl: Option<i64>,
    pub ev_noise_mode: Option<i64>,
    pub ev_delay_ms: Option<f64>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundleBenchReadinessCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleBenchReadinessReport {
    pub status: Option<String>,
    pub failed_count: Option<u64>,
    pub degraded_count: Option<u64>,
    pub passed_count: Option<u64>,
    pub checks: Vec<SupportBundleBenchReadinessCheck>,
}

#[derive(Serialize)]
pub struct SupportBundleLogRecordPreview {
    pub line_number: usize,
    pub sequence: Option<u64>,
    pub timestamp_utc: Option<String>,
    pub timestamp_us: Option<u64>,
    pub status: Option<String>,
    pub reason: Option<String>,
    pub tile_id: Option<String>,
    pub map_id: Option<String>,
    pub confidence: Option<f64>,
    pub inliers: Option<u64>,
    pub reprojection_error_px: Option<f64>,
    pub external_position_status: Option<String>,
    pub external_position_message_type: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleLogPreview {
    pub name: String,
    pub records: Vec<SupportBundleLogRecordPreview>,
    pub truncated: bool,
}

#[derive(Serialize)]
pub struct SupportBundleImagePreview {
    pub name: String,
    pub path: String,
    pub mime_type: String,
    pub size_bytes: u64,
    pub base64_data: String,
}

#[derive(Serialize)]
pub struct SupportBundleDetails {
    pub manifest: serde_json::Value,
    pub metadata: Option<serde_json::Value>,
    pub bundle_health: Option<serde_json::Value>,
    pub logs: Vec<SupportBundleLogSummary>,
    pub log_previews: Vec<SupportBundleLogPreview>,
    pub image_previews: Vec<SupportBundleImagePreview>,
    pub replay_reports: Vec<SupportBundleReplayReport>,
    pub px4_evidence_reports: Vec<SupportBundlePx4EvidenceReport>,
    pub px4_param_reports: Vec<SupportBundlePx4ParamReport>,
    pub bench_readiness: Option<SupportBundleBenchReadinessReport>,
    pub entry_count: usize,
}

#[tauri::command]
pub fn read_yaml_config(path: String) -> Result<serde_json::Value, String> {
    let text = std::fs::read_to_string(&path)
        .with_context(|| format!("Cannot read {path}"))
        .map_err(|e| e.to_string())?;
    let val: serde_yaml::Value = serde_yaml::from_str(&text).map_err(|e| e.to_string())?;
    serde_json::to_value(val).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn write_yaml_config(path: String, data: serde_json::Value) -> Result<(), String> {
    let yaml_val: serde_yaml::Value = serde_json::from_value(data).map_err(|e| e.to_string())?;
    let text = serde_yaml::to_string(&yaml_val).map_err(|e| e.to_string())?;
    if let Some(parent) = Path::new(&path).parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn list_yaml_configs(dir: String) -> Result<Vec<String>, String> {
    let path = Path::new(&dir);
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(path).map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) == Some("yaml") {
            files.push(p.to_string_lossy().into_owned());
        }
    }
    Ok(files)
}

#[tauri::command]
pub fn list_support_bundles(dir: String) -> Result<Vec<SupportBundleFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("zip") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(SupportBundleFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("support.zip")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            summary: read_support_bundle_summary(&p),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn reveal_support_bundle(path: String) -> Result<(), String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Err(format!("Support bundle does not exist: {}", path.display()));
    }
    reveal_path(&path).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn delete_support_bundle(path: String) -> Result<(), String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    if path.extension().and_then(|ext| ext.to_str()) != Some("zip") {
        return Err("Only support bundle ZIP files can be deleted from the app.".to_string());
    }
    if !path.exists() {
        return Ok(());
    }
    std::fs::remove_file(&path)
        .with_context(|| format!("Cannot delete support bundle {}", path.display()))
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn read_support_bundle_details(path: String) -> Result<SupportBundleDetails, String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    let file = File::open(&path)
        .with_context(|| format!("Cannot open support bundle {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut archive = ZipArchive::new(file).map_err(|e| e.to_string())?;
    let entry_count = archive.len();
    let manifest = read_json_entry(&mut archive, "support_manifest.json")?
        .ok_or_else(|| "Missing support_manifest.json".to_string())?;
    let mut logs = Vec::new();
    let mut log_previews = Vec::new();
    let mut image_previews = Vec::new();
    let mut replay_reports = Vec::new();
    let mut px4_evidence_reports = Vec::new();
    let mut px4_param_reports = Vec::new();
    let mut bench_readiness = manifest
        .get("bench_readiness")
        .map(bench_readiness_report_from_json);
    for index in 0..archive.len() {
        let (name, size_bytes) = {
            let entry = archive.by_index(index).map_err(|e| e.to_string())?;
            (entry.name().to_string(), entry.size())
        };
        if name.starts_with("summaries/")
            && name.ends_with(".summary.json")
            && !name.contains("/replay_gates/")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                logs.push(log_summary_from_json(&name, &value));
            }
        } else if name.starts_with("logs/") && name.ends_with(".jsonl") {
            if let Some(preview) = read_log_preview_entry(&mut archive, &name)? {
                log_previews.push(preview);
            }
        } else if name.starts_with("summaries/replay_gates/") && name.ends_with(".gate.json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                replay_reports.push(replay_report_from_json(&value));
            }
        } else if name.starts_with("summaries/px4_sitl_evidence/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                px4_evidence_reports.push(px4_evidence_report_from_json(&value));
            }
        } else if name.starts_with("summaries/px4_params/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                px4_param_reports.push(px4_param_report_from_json(&value));
            }
        } else if name == "summaries/bench_readiness.json" {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                bench_readiness = Some(bench_readiness_report_from_json(&value));
            }
        } else if image_previews.len() < IMAGE_PREVIEW_LIMIT
            && should_preview_image_entry(&name, size_bytes)
        {
            if let Some(preview) = read_image_preview_entry(&mut archive, &name, size_bytes)? {
                image_previews.push(preview);
            }
        }
    }
    Ok(SupportBundleDetails {
        metadata: manifest.get("metadata").cloned(),
        bundle_health: manifest.pointer("/bundle/health").cloned(),
        logs,
        log_previews,
        image_previews,
        replay_reports,
        px4_evidence_reports,
        px4_param_reports,
        bench_readiness,
        entry_count,
        manifest,
    })
}

fn read_json_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
) -> Result<Option<serde_json::Value>, String> {
    let mut entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut text = String::new();
    entry.read_to_string(&mut text).map_err(|e| e.to_string())?;
    serde_json::from_str(&text)
        .map(Some)
        .with_context(|| format!("Invalid JSON in support bundle entry {name}"))
        .map_err(|e| e.to_string())
}

fn log_summary_from_json(name: &str, value: &serde_json::Value) -> SupportBundleLogSummary {
    SupportBundleLogSummary {
        name: Path::new(name)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(name)
            .to_string(),
        total_records: value.get("total_records").and_then(|value| value.as_u64()),
        accepted_rate: value.get("accepted_rate").and_then(|value| value.as_f64()),
        status_counts: value.get("status_counts").cloned(),
        reason_counts: value.get("reason_counts").cloned(),
        external_position: value.get("external_position").cloned(),
    }
}

fn read_log_preview_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
) -> Result<Option<SupportBundleLogPreview>, String> {
    let entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut records = Vec::new();
    let mut truncated = false;
    let reader = BufReader::new(entry);
    for (line_index, line) in reader.lines().enumerate() {
        let line = line.map_err(|e| e.to_string())?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if records.len() >= LOG_PREVIEW_LIMIT {
            truncated = true;
            break;
        }
        let value = match serde_json::from_str::<serde_json::Value>(trimmed) {
            Ok(value) => value,
            Err(_) => {
                records.push(SupportBundleLogRecordPreview {
                    line_number: line_index + 1,
                    sequence: None,
                    timestamp_utc: None,
                    timestamp_us: None,
                    status: Some("invalid_json".to_string()),
                    reason: Some("Could not parse JSONL record".to_string()),
                    tile_id: None,
                    map_id: None,
                    confidence: None,
                    inliers: None,
                    reprojection_error_px: None,
                    external_position_status: None,
                    external_position_message_type: None,
                });
                continue;
            }
        };
        records.push(log_record_preview_from_json(line_index + 1, &value));
    }
    Ok(Some(SupportBundleLogPreview {
        name: Path::new(name)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(name)
            .to_string(),
        records,
        truncated,
    }))
}

fn log_record_preview_from_json(
    line_number: usize,
    value: &serde_json::Value,
) -> SupportBundleLogRecordPreview {
    let result = value.get("result").unwrap_or(value);
    let external_position = value.get("external_position_health");
    SupportBundleLogRecordPreview {
        line_number,
        sequence: value
            .get("sequence")
            .or_else(|| result.get("sequence"))
            .and_then(|value| value.as_u64()),
        timestamp_utc: json_string(
            value
                .get("timestamp_utc")
                .or_else(|| result.get("timestamp_utc")),
        ),
        timestamp_us: value
            .get("timestamp_us")
            .or_else(|| result.get("timestamp_us"))
            .and_then(|value| value.as_u64()),
        status: json_string(result.get("status")),
        reason: json_string(result.get("reason")),
        tile_id: json_string(result.get("tile_id")),
        map_id: json_string(result.get("map_id")),
        confidence: result.get("confidence").and_then(|value| value.as_f64()),
        inliers: result.get("inliers").and_then(|value| value.as_u64()),
        reprojection_error_px: result
            .get("reprojection_error_px")
            .and_then(|value| value.as_f64()),
        external_position_status: external_position
            .and_then(|value| json_string(value.get("status"))),
        external_position_message_type: external_position
            .and_then(|value| json_string(value.get("message_type"))),
    }
}

fn replay_report_from_json(value: &serde_json::Value) -> SupportBundleReplayReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let metrics = value.get("metrics");
    SupportBundleReplayReport {
        case_name: json_string(value.get("case_name")),
        expected: json_string(value.get("expected")),
        status: json_string(value.get("status")),
        accepted_rate: metrics
            .and_then(|value| value.get("accepted_rate"))
            .and_then(|value| value.as_f64()),
        total_records: metrics
            .and_then(|value| value.get("total_records"))
            .and_then(|value| value.as_u64()),
        issues,
    }
}

fn px4_evidence_report_from_json(value: &serde_json::Value) -> SupportBundlePx4EvidenceReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundlePx4EvidenceReport {
        status: json_string(value.get("status")),
        expected_message: json_string(value.get("expected_message")),
        sample_count: value
            .pointer("/listener/sample_count")
            .and_then(|value| value.as_u64()),
        latest_sample_age_s: value
            .pointer("/listener/latest_sample_age_s")
            .and_then(|value| value.as_f64()),
        last_position: value.pointer("/listener/last_position").cloned(),
        mavlink_version: value
            .pointer("/mavlink_status/mavlink_version")
            .and_then(|value| value.as_u64()),
        has_udp_14550: value
            .pointer("/mavlink_status/has_udp_14550")
            .and_then(|value| value.as_bool()),
        issues,
    }
}

fn px4_param_report_from_json(value: &serde_json::Value) -> SupportBundlePx4ParamReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundlePx4ParamReport {
        status: json_string(value.get("status")),
        ev_ctrl: value
            .pointer("/parameters/EKF2_EV_CTRL")
            .and_then(|value| value.as_i64()),
        hgt_ref: value
            .pointer("/parameters/EKF2_HGT_REF")
            .and_then(|value| value.as_i64()),
        gps_ctrl: value
            .pointer("/parameters/EKF2_GPS_CTRL")
            .and_then(|value| value.as_i64()),
        ev_noise_mode: value
            .pointer("/parameters/EKF2_EV_NOISE_MD")
            .and_then(|value| value.as_i64()),
        ev_delay_ms: value
            .pointer("/parameters/EKF2_EV_DELAY")
            .and_then(|value| value.as_f64()),
        issues,
    }
}

fn bench_readiness_report_from_json(value: &serde_json::Value) -> SupportBundleBenchReadinessReport {
    let checks = value
        .get("checks")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| SupportBundleBenchReadinessCheck {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleBenchReadinessReport {
        status: json_string(value.get("status")),
        failed_count: value
            .pointer("/summary/failed")
            .and_then(|value| value.as_u64()),
        degraded_count: value
            .pointer("/summary/degraded")
            .and_then(|value| value.as_u64()),
        passed_count: value
            .pointer("/summary/passed")
            .and_then(|value| value.as_u64()),
        checks,
    }
}

fn image_mime_type(name: &str) -> Option<&'static str> {
    match Path::new(name)
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.to_ascii_lowercase())
        .as_deref()
    {
        Some("png") => Some("image/png"),
        Some("jpg") | Some("jpeg") => Some("image/jpeg"),
        Some("webp") => Some("image/webp"),
        Some("gif") => Some("image/gif"),
        Some("bmp") => Some("image/bmp"),
        _ => None,
    }
}

fn should_preview_image_entry(name: &str, size_bytes: u64) -> bool {
    if size_bytes == 0 || size_bytes > IMAGE_PREVIEW_MAX_BYTES || image_mime_type(name).is_none() {
        return false;
    }
    let lower = name.to_ascii_lowercase();
    if lower.starts_with("bundle/ortho/")
        || lower.starts_with("bundle/imagery/")
        || lower.starts_with("bundle/index/")
        || lower.starts_with("bundle/elevation/")
        || lower.contains("/imagery/tiles/")
        || lower.contains("/index/descriptors/")
        || lower.ends_with("/satellite.png")
    {
        return false;
    }
    lower.starts_with("extras/")
        || lower.starts_with("logs/")
        || lower.starts_with("summaries/")
        || [
            "camera",
            "frame",
            "debug",
            "match",
            "replay",
            "smoke",
            "calibration",
            "preview",
        ]
        .iter()
        .any(|token| lower.contains(token))
}

fn read_image_preview_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
    size_bytes: u64,
) -> Result<Option<SupportBundleImagePreview>, String> {
    let mime_type = match image_mime_type(name) {
        Some(value) => value,
        None => return Ok(None),
    };
    if !should_preview_image_entry(name, size_bytes) {
        return Ok(None);
    }
    let mut entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut bytes = Vec::new();
    entry.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > IMAGE_PREVIEW_MAX_BYTES {
        return Ok(None);
    }
    Ok(Some(SupportBundleImagePreview {
        name: Path::new(name)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(name)
            .to_string(),
        path: name.to_string(),
        mime_type: mime_type.to_string(),
        size_bytes,
        base64_data: general_purpose::STANDARD.encode(bytes),
    }))
}

fn reveal_path(path: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg("-R")
            .arg(path)
            .status()
            .context("Failed to run macOS open command")?;
        if status.success() {
            return Ok(());
        }
        return Err(anyhow!("macOS open command failed with {status}"));
    }

    #[cfg(target_os = "windows")]
    {
        let status = if path.is_file() {
            Command::new("explorer")
                .arg(format!("/select,{}", path.display()))
                .status()
                .context("Failed to run Windows Explorer")?
        } else {
            Command::new("explorer")
                .arg(path)
                .status()
                .context("Failed to run Windows Explorer")?
        };
        if status.success() {
            return Ok(());
        }
        return Err(anyhow!("Windows Explorer failed with {status}"));
    }

    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        let target = if path.is_file() {
            path.parent().unwrap_or(path)
        } else {
            path
        };
        let status = Command::new("xdg-open")
            .arg(target)
            .status()
            .context("Failed to run xdg-open")?;
        if status.success() {
            return Ok(());
        }
        Err(anyhow!("xdg-open failed with {status}"))
    }
}

fn read_support_bundle_summary(path: &Path) -> Option<SupportBundleSummary> {
    let file = File::open(path).ok()?;
    let mut archive = ZipArchive::new(file).ok()?;
    let mut manifest_entry = archive.by_name("support_manifest.json").ok()?;
    let mut text = String::new();
    manifest_entry.read_to_string(&mut text).ok()?;
    let manifest: serde_json::Value = serde_json::from_str(&text).ok()?;
    support_summary_from_manifest(&manifest)
}

fn json_string(value: Option<&serde_json::Value>) -> Option<String> {
    value
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
}

fn support_summary_from_manifest(manifest: &serde_json::Value) -> Option<SupportBundleSummary> {
    let health = manifest.pointer("/bundle/health");
    let provenance = manifest.pointer("/bundle/health/source_provenance");
    let source_name = json_string(provenance.and_then(|value| value.get("original_file")))
        .or_else(|| json_string(provenance.and_then(|value| value.get("map_name"))))
        .or_else(|| json_string(provenance.and_then(|value| value.get("orthophoto_path"))));
    let summary = SupportBundleSummary {
        bundle_id: json_string(manifest.pointer("/bundle/bundle_id")),
        bundle_health_status: json_string(health.and_then(|value| value.get("status"))),
        checksum_status: json_string(manifest.pointer("/bundle/health/checksums/status")),
        covered_file_count: manifest
            .pointer("/bundle/health/checksums/covered_file_count")
            .or_else(|| manifest.pointer("/bundle/health/checksums/entry_count"))
            .and_then(|value| value.as_u64()),
        elevation_status: json_string(manifest.pointer("/bundle/health/elevation/status")),
        elevation_asset_count: manifest
            .pointer("/bundle/health/elevation/asset_count")
            .and_then(|value| value.as_u64()),
        vertical_sanity_ready: manifest
            .pointer("/bundle/health/elevation/vertical_sanity_ready")
            .and_then(|value| value.as_bool()),
        map_source: json_string(provenance.and_then(|value| value.get("map_source"))),
        source_name,
        georef_source: json_string(provenance.and_then(|value| value.get("georef_source"))),
        georef_crs: json_string(provenance.and_then(|value| value.get("georef_crs"))),
        georef_confidence: provenance
            .and_then(|value| value.get("georef_confidence"))
            .and_then(|value| value.as_f64()),
        replay_gate_status: json_string(manifest.pointer("/replay_gates/status")),
        replay_case_count: manifest
            .pointer("/replay_gates/case_count")
            .and_then(|value| value.as_u64()),
        px4_sitl_evidence_status: json_string(manifest.pointer("/px4_sitl_evidence/status")),
        px4_sitl_sample_count: manifest
            .pointer("/px4_sitl_evidence/listener/sample_count")
            .and_then(|value| value.as_u64()),
        px4_params_status: json_string(manifest.pointer("/px4_params/status")),
        px4_ev_ctrl: manifest
            .pointer("/px4_params/parameters/EKF2_EV_CTRL")
            .and_then(|value| value.as_i64()),
        bench_readiness_status: json_string(manifest.pointer("/bench_readiness/status")),
        bench_readiness_failed_count: manifest
            .pointer("/bench_readiness/summary/failed")
            .and_then(|value| value.as_u64()),
        bench_readiness_degraded_count: manifest
            .pointer("/bench_readiness/summary/degraded")
            .and_then(|value| value.as_u64()),
    };
    if summary.bundle_id.is_none()
        && summary.bundle_health_status.is_none()
        && summary.checksum_status.is_none()
        && summary.elevation_status.is_none()
        && summary.replay_gate_status.is_none()
        && summary.px4_sitl_evidence_status.is_none()
        && summary.px4_params_status.is_none()
        && summary.bench_readiness_status.is_none()
    {
        return None;
    }
    Some(summary)
}

fn expand_local_path(path: &str) -> Result<PathBuf> {
    if path.is_empty() {
        return Err(anyhow!("Local directory is empty"));
    }
    if path == "~" {
        return std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("HOME is not set"));
    }
    if let Some(rest) = path.strip_prefix("~/") {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("HOME is not set"))?;
        return Ok(home.join(rest));
    }
    Ok(PathBuf::from(path))
}

#[cfg(test)]
mod tests {
    use super::{
        delete_support_bundle, expand_local_path, read_support_bundle_details,
        support_summary_from_manifest,
    };
    use std::fs::File;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};
    use zip::write::SimpleFileOptions;

    #[test]
    fn expands_home_prefixed_support_bundle_dir() {
        let expanded = expand_local_path("~/DroneTransfer/from-pi/support-bundles")
            .expect("expand support path");
        assert!(expanded.ends_with("DroneTransfer/from-pi/support-bundles"));
    }

    #[test]
    fn extracts_support_bundle_summary_from_manifest() {
        let manifest = serde_json::json!({
            "bundle": {
                "bundle_id": "mission-bundle",
                "health": {
                    "status": "passed",
                    "checksums": {
                        "status": "passed",
                        "covered_file_count": 12
                    },
                    "source_provenance": {
                        "map_source": "uploaded_geotiff",
                        "original_file": "field-map.tif",
                        "georef_source": "geotiff_embedded",
                        "georef_crs": "EPSG:4326",
                        "georef_confidence": 0.95
                    },
                    "elevation": {
                        "status": "passed",
                        "asset_count": 2,
                        "vertical_sanity_ready": true
                    }
                }
            },
            "replay_gates": {
                "status": "passed",
                "case_count": 3
            },
            "px4_sitl_evidence": {
                "status": "passed",
                "listener": {
                    "sample_count": 2
                }
            },
            "px4_params": {
                "status": "degraded",
                "parameters": {
                    "EKF2_EV_CTRL": 1
                }
            },
            "bench_readiness": {
                "status": "degraded",
                "summary": {
                    "failed": 0,
                    "degraded": 1,
                    "passed": 4
                }
            }
        });
        let summary = support_summary_from_manifest(&manifest).expect("support summary");
        assert_eq!(summary.bundle_id.as_deref(), Some("mission-bundle"));
        assert_eq!(summary.bundle_health_status.as_deref(), Some("passed"));
        assert_eq!(summary.checksum_status.as_deref(), Some("passed"));
        assert_eq!(summary.covered_file_count, Some(12));
        assert_eq!(summary.elevation_status.as_deref(), Some("passed"));
        assert_eq!(summary.elevation_asset_count, Some(2));
        assert_eq!(summary.vertical_sanity_ready, Some(true));
        assert_eq!(summary.map_source.as_deref(), Some("uploaded_geotiff"));
        assert_eq!(summary.source_name.as_deref(), Some("field-map.tif"));
        assert_eq!(summary.replay_gate_status.as_deref(), Some("passed"));
        assert_eq!(summary.replay_case_count, Some(3));
        assert_eq!(summary.px4_sitl_evidence_status.as_deref(), Some("passed"));
        assert_eq!(summary.px4_sitl_sample_count, Some(2));
        assert_eq!(summary.px4_params_status.as_deref(), Some("degraded"));
        assert_eq!(summary.px4_ev_ctrl, Some(1));
        assert_eq!(summary.bench_readiness_status.as_deref(), Some("degraded"));
        assert_eq!(summary.bench_readiness_failed_count, Some(0));
        assert_eq!(summary.bench_readiness_degraded_count, Some(1));
    }

    #[test]
    fn delete_support_bundle_rejects_non_zip_files() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("drone-support-delete-{stamp}.txt"));
        std::fs::write(&path, "not a zip").expect("write temp file");
        let result = delete_support_bundle(path.to_string_lossy().into_owned());
        let _ = std::fs::remove_file(&path);
        assert!(result.is_err());
    }

    #[test]
    fn reads_support_bundle_details_from_zip() {
        const TINY_PNG: &[u8] = &[
            137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1,
            8, 6, 0, 0, 0, 31, 21, 196, 137, 0, 0, 0, 13, 73, 68, 65, 84, 120, 156, 99, 248, 255,
            255, 63, 0, 5, 254, 2, 254, 167, 53, 129, 132, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96,
            130,
        ];
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("drone-support-details-{stamp}.zip"));
        {
            let file = File::create(&path).expect("create zip");
            let mut zip = zip::ZipWriter::new(file);
            let options = SimpleFileOptions::default();
            zip.start_file("support_manifest.json", options)
                .expect("manifest entry");
            zip.write_all(
                serde_json::json!({
                    "metadata": {"vision_nav": {"project_version": "0.1.0"}},
                    "bundle": {"health": {"status": "passed"}}
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write manifest");
            zip.start_file("summaries/terrain_matches.summary.json", options)
                .expect("summary entry");
            zip.write_all(
                serde_json::json!({
                    "total_records": 4,
                    "accepted_rate": 0.5,
                    "status_counts": {"accepted": 2, "rejected": 2}
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write summary");
            zip.start_file("logs/terrain_matches.jsonl", options)
                .expect("log entry");
            zip.write_all(
                [
                    serde_json::json!({
                        "sequence": 1,
                        "result": {
                            "status": "accepted",
                            "tile_id": "tile_001",
                            "confidence": 0.8,
                            "inliers": 24,
                            "reprojection_error_px": 1.5
                        },
                        "external_position_health": {
                            "status": "healthy",
                            "message_type": "odometry"
                        }
                    })
                    .to_string(),
                    serde_json::json!({
                        "sequence": 2,
                        "result": {
                            "status": "rejected",
                            "reason": "low_inliers"
                        }
                    })
                    .to_string(),
                ]
                .join("\n")
                .as_bytes(),
            )
            .expect("write log");
            zip.start_file("summaries/replay_gates/unit.gate.json", options)
                .expect("gate entry");
            zip.write_all(
                serde_json::json!({
                    "case_name": "unit",
                    "expected": "good_map",
                    "status": "failed",
                    "metrics": {"accepted_rate": 0.25, "total_records": 4},
                    "issues": [{"message": "low accepted rate"}]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write gate");
            zip.start_file("summaries/px4_sitl_evidence/receiver_evidence.json", options)
                .expect("px4 evidence entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "expected_message": "odometry",
                    "listener": {
                        "sample_count": 2,
                        "latest_sample_age_s": 0.02,
                        "last_position": [0.35, 0.3, -1.5]
                    },
                    "mavlink_status": {
                        "mavlink_version": 2,
                        "has_udp_14550": true
                    },
                    "issues": []
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write px4 evidence");
            zip.start_file("summaries/px4_params/param_check.json", options)
                .expect("px4 params entry");
            zip.write_all(
                serde_json::json!({
                    "status": "degraded",
                    "parameters": {
                        "EKF2_EV_CTRL": 1,
                        "EKF2_HGT_REF": 0,
                        "EKF2_GPS_CTRL": 7,
                        "EKF2_EV_NOISE_MD": 0,
                        "EKF2_EV_DELAY": 80.0
                    },
                    "issues": [{"message": "confirm extrinsics"}]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write px4 params");
            zip.start_file("summaries/bench_readiness.json", options)
                .expect("bench readiness entry");
            zip.write_all(
                serde_json::json!({
                    "status": "degraded",
                    "summary": {"failed": 0, "degraded": 1, "passed": 4},
                    "checks": [
                        {"name": "bundle_health", "status": "passed", "message": "Terrain bundle health passed."},
                        {"name": "px4_params", "status": "degraded", "message": "PX4 parameter check is degraded."}
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write bench readiness");
            zip.start_file("extras/camera-health/frame.png", options)
                .expect("image entry");
            zip.write_all(TINY_PNG).expect("write image");
            zip.start_file("bundle/ortho/map.png", options)
                .expect("map asset entry");
            zip.write_all(TINY_PNG).expect("write map asset");
            zip.finish().expect("finish zip");
        }
        let details = read_support_bundle_details(path.to_string_lossy().into_owned())
            .expect("read support details");
        let _ = std::fs::remove_file(&path);
        assert_eq!(details.entry_count, 9);
        assert_eq!(details.logs.len(), 1);
        assert_eq!(details.logs[0].total_records, Some(4));
        assert_eq!(details.log_previews.len(), 1);
        assert_eq!(details.log_previews[0].records.len(), 2);
        assert_eq!(details.image_previews.len(), 1);
        assert_eq!(details.image_previews[0].name, "frame.png");
        assert_eq!(details.image_previews[0].mime_type, "image/png");
        assert!(!details.image_previews[0].base64_data.is_empty());
        assert_eq!(
            details.log_previews[0].records[0].tile_id.as_deref(),
            Some("tile_001")
        );
        assert_eq!(
            details.log_previews[0].records[1].reason.as_deref(),
            Some("low_inliers")
        );
        assert_eq!(details.replay_reports.len(), 1);
        assert_eq!(details.replay_reports[0].status.as_deref(), Some("failed"));
        assert_eq!(
            details.replay_reports[0].issues,
            vec!["low accepted rate".to_string()]
        );
        assert_eq!(details.px4_evidence_reports.len(), 1);
        assert_eq!(details.px4_evidence_reports[0].status.as_deref(), Some("passed"));
        assert_eq!(details.px4_evidence_reports[0].sample_count, Some(2));
        assert_eq!(details.px4_evidence_reports[0].has_udp_14550, Some(true));
        assert_eq!(details.px4_param_reports.len(), 1);
        assert_eq!(details.px4_param_reports[0].status.as_deref(), Some("degraded"));
        assert_eq!(details.px4_param_reports[0].ev_ctrl, Some(1));
        assert_eq!(details.px4_param_reports[0].hgt_ref, Some(0));
        let readiness = details.bench_readiness.expect("bench readiness report");
        assert_eq!(readiness.status.as_deref(), Some("degraded"));
        assert_eq!(readiness.degraded_count, Some(1));
        assert_eq!(readiness.checks.len(), 2);
        assert_eq!(readiness.checks[1].name.as_deref(), Some("px4_params"));
    }
}
