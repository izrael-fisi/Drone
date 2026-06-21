use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;
use zip::ZipArchive;

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
    use super::{expand_local_path, support_summary_from_manifest};

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
}
