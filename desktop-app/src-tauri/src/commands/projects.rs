//! Site projects: named map workspaces with placed items (cameras, sensors,
//! geofences, ...). The schema is owned by the frontend
//! (`src/platform/projects/`); Rust stores the document as JSON on disk.

use std::path::PathBuf;

fn projects_path() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("DroneVisionNav")
        .join("projects.json")
}

#[tauri::command]
pub fn load_projects() -> Result<serde_json::Value, String> {
    let path = projects_path();
    if !path.exists() {
        return Ok(serde_json::Value::Null);
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| format!("projects.json is not valid JSON: {e}"))
}

/// Write via a temporary file and rename, so a crash mid-write never leaves a
/// truncated projects.json behind.
#[tauri::command]
pub fn save_projects(document: serde_json::Value) -> Result<(), String> {
    let path = projects_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&document).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}
