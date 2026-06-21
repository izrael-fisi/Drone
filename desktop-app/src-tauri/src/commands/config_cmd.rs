use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

#[derive(Serialize)]
pub struct SupportBundleFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
}

#[tauri::command]
pub fn read_yaml_config(path: String) -> Result<serde_json::Value, String> {
    let text = std::fs::read_to_string(&path)
        .with_context(|| format!("Cannot read {path}"))
        .map_err(|e| e.to_string())?;
    let val: serde_yaml::Value = serde_yaml::from_str(&text)
        .map_err(|e| e.to_string())?;
    serde_json::to_value(val).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn write_yaml_config(path: String, data: serde_json::Value) -> Result<(), String> {
    let yaml_val: serde_yaml::Value = serde_json::from_value(data)
        .map_err(|e| e.to_string())?;
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
            name: p.file_name().and_then(|name| name.to_str()).unwrap_or("support.zip").to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
        });
    }
    files.sort_by(|a, b| b.modified_unix_ms.cmp(&a.modified_unix_ms).then_with(|| a.name.cmp(&b.name)));
    Ok(files)
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
    use super::expand_local_path;

    #[test]
    fn expands_home_prefixed_support_bundle_dir() {
        let expanded = expand_local_path("~/DroneTransfer/from-pi/support-bundles")
            .expect("expand support path");
        assert!(expanded.ends_with("DroneTransfer/from-pi/support-bundles"));
    }
}
