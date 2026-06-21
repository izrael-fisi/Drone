use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::UNIX_EPOCH;
use zip::ZipArchive;

const LOG_PREVIEW_LIMIT: usize = 5;

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
pub struct SupportBundleDetails {
    pub manifest: serde_json::Value,
    pub metadata: Option<serde_json::Value>,
    pub bundle_health: Option<serde_json::Value>,
    pub logs: Vec<SupportBundleLogSummary>,
    pub log_previews: Vec<SupportBundleLogPreview>,
    pub replay_reports: Vec<SupportBundleReplayReport>,
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
    let mut replay_reports = Vec::new();
    for index in 0..archive.len() {
        let name = {
            let entry = archive.by_index(index).map_err(|e| e.to_string())?;
            entry.name().to_string()
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
        }
    }
    Ok(SupportBundleDetails {
        metadata: manifest.get("metadata").cloned(),
        bundle_health: manifest.pointer("/bundle/health").cloned(),
        logs,
        log_previews,
        replay_reports,
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
    };
    if summary.bundle_id.is_none()
        && summary.bundle_health_status.is_none()
        && summary.checksum_status.is_none()
        && summary.elevation_status.is_none()
        && summary.replay_gate_status.is_none()
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
            zip.finish().expect("finish zip");
        }
        let details = read_support_bundle_details(path.to_string_lossy().into_owned())
            .expect("read support details");
        let _ = std::fs::remove_file(&path);
        assert_eq!(details.entry_count, 4);
        assert_eq!(details.logs.len(), 1);
        assert_eq!(details.logs[0].total_records, Some(4));
        assert_eq!(details.log_previews.len(), 1);
        assert_eq!(details.log_previews[0].records.len(), 2);
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
    }
}
