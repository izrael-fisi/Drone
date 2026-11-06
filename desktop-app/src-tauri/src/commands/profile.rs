use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use super::{backend, secrets};

fn profile_path() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("DroneVisionNav")
        .join("profile.json")
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Profile {
    pub name: String,
    pub email: String,
    pub org: String,
    pub accent_color: String,
    pub onboarding_complete: bool,
    #[serde(default)]
    pub mapbox_key: Option<String>,
    #[serde(default)]
    pub bing_key: Option<String>,
    #[serde(default)]
    pub max_map_area_km2: Option<f64>,
    #[serde(default)]
    pub max_map_download_size_gb: Option<f64>,
    // Proxigo cloud auth — persisted across restarts
    #[serde(default)]
    pub proxigo_access_token: Option<String>,
    #[serde(default)]
    pub proxigo_refresh_token: Option<String>,
    #[serde(default)]
    pub proxigo_token_expires_at: Option<f64>,
    #[serde(default)]
    pub proxigo_user_id: Option<String>,
    #[serde(default)]
    pub proxigo_email: Option<String>,
    #[serde(default)]
    pub proxigo_module_serial: Option<String>,
}

impl Default for Profile {
    fn default() -> Self {
        Self {
            name: String::new(),
            email: String::new(),
            org: String::new(),
            accent_color: "#FF6600".to_string(),
            onboarding_complete: false,
            mapbox_key: None,
            bing_key: None,
            max_map_area_km2: None,
            max_map_download_size_gb: Some(20.0),
            proxigo_access_token: None,
            proxigo_refresh_token: None,
            proxigo_token_expires_at: None,
            proxigo_user_id: None,
            proxigo_email: None,
            proxigo_module_serial: None,
        }
    }
}

fn token_secret_name(kind: &str) -> String {
    format!("{}:{}", backend::current_connection().id, kind)
}

/// Move the Proxigo session tokens into the OS credential store and blank
/// them in the copy written to disk. If the credential store is unavailable
/// the tokens stay in the file so sign-in keeps working.
fn store_tokens_securely(profile: &Profile) -> Profile {
    let mut on_disk = profile.clone();
    for (kind, value, field) in [
        ("access_token", profile.proxigo_access_token.as_deref(), &mut on_disk.proxigo_access_token),
        ("refresh_token", profile.proxigo_refresh_token.as_deref(), &mut on_disk.proxigo_refresh_token),
    ] {
        let name = token_secret_name(kind);
        match value {
            Some(token) => match secrets::set_secret(&name, token) {
                Ok(()) => *field = None,
                Err(error) => eprintln!("Proxigo token kept in profile file: {error}"),
            },
            None => secrets::delete_secret(&name),
        }
    }
    on_disk
}

fn write_profile_file(profile: &Profile) -> Result<(), String> {
    let path = profile_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(profile).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn load_profile() -> Result<Profile, String> {
    let path = profile_path();
    if !path.exists() {
        return Ok(Profile::default());
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut profile: Profile = serde_json::from_str(&text).map_err(|e| e.to_string())?;

    // Migrate tokens left in the file by older versions.
    if profile.proxigo_access_token.is_some() || profile.proxigo_refresh_token.is_some() {
        let on_disk = store_tokens_securely(&profile);
        if on_disk.proxigo_access_token.is_none() && on_disk.proxigo_refresh_token.is_none() {
            write_profile_file(&on_disk)?;
        }
        return Ok(profile);
    }

    profile.proxigo_access_token = secrets::get_secret(&token_secret_name("access_token"));
    profile.proxigo_refresh_token = secrets::get_secret(&token_secret_name("refresh_token"));
    Ok(profile)
}

#[tauri::command]
pub fn save_profile(profile: Profile) -> Result<(), String> {
    write_profile_file(&store_tokens_securely(&profile))
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(tag = "type")]
pub enum DeviceAuth {
    Password {
        /// Empty in devices.json when the password is in the OS credential store.
        #[serde(default)]
        password: String,
    },
    Key {
        key_path: String,
        #[serde(default)]
        passphrase: Option<String>,
    },
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Device {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub host: Option<String>,
    pub port: Option<u16>,
    pub username: Option<String>,
    pub auth: Option<DeviceAuth>,
    pub remote_project_path: Option<String>,
    #[serde(default)]
    pub known_fingerprint: Option<String>,
    #[serde(default)]
    pub mavlink_endpoint: Option<String>,
    #[serde(default)]
    pub autopilot: Option<String>,
    #[serde(default)]
    pub vision_pipeline: Option<String>,
    #[serde(default)]
    pub feature_method: Option<String>,
}

fn devices_path() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("DroneVisionNav")
        .join("devices.json")
}

fn device_secret_name(device_id: &str) -> String {
    format!("device:{device_id}:ssh-secret")
}

/// The SSH password or key passphrase of a device, if it has one.
fn device_secret(device: &Device) -> Option<&str> {
    match &device.auth {
        Some(DeviceAuth::Password { password }) if !password.is_empty() => Some(password),
        Some(DeviceAuth::Key { passphrase: Some(p), .. }) if !p.is_empty() => Some(p),
        _ => None,
    }
}

fn set_device_secret(device: &mut Device, value: Option<String>) {
    match &mut device.auth {
        Some(DeviceAuth::Password { password }) => *password = value.unwrap_or_default(),
        Some(DeviceAuth::Key { passphrase, .. }) => *passphrase = value,
        None => {}
    }
}

fn read_devices_file() -> Result<Vec<Device>, String> {
    let path = devices_path();
    if !path.exists() {
        return Ok(vec![]);
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

/// Write devices with SSH passwords/passphrases moved to the OS credential
/// store. A secret stays in the file only if the credential store fails.
fn write_devices_file(devices: &[Device]) -> Result<(), String> {
    let previous_ids: Vec<String> = read_devices_file().unwrap_or_default().into_iter().map(|d| d.id).collect();
    let mut on_disk = devices.to_vec();
    for device in &mut on_disk {
        let name = device_secret_name(&device.id);
        match device_secret(device).map(str::to_string) {
            Some(secret) => match secrets::set_secret(&name, &secret) {
                Ok(()) => set_device_secret(device, None),
                Err(error) => eprintln!("SSH secret for {} kept in devices.json: {error}", device.id),
            },
            None => secrets::delete_secret(&name),
        }
    }
    for removed in previous_ids.iter().filter(|id| !devices.iter().any(|d| &d.id == *id)) {
        secrets::delete_secret(&device_secret_name(removed));
    }

    let path = devices_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&on_disk).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn load_devices() -> Result<Vec<Device>, String> {
    let mut devices = read_devices_file()?;
    // Migrate secrets written in plain text by older versions.
    if devices.iter().any(|d| device_secret(d).is_some()) {
        write_devices_file(&devices)?;
    }
    for device in &mut devices {
        if device_secret(device).is_none() {
            if let Some(secret) = secrets::get_secret(&device_secret_name(&device.id)) {
                set_device_secret(device, Some(secret));
            }
        }
    }
    Ok(devices)
}

#[tauri::command]
pub fn save_devices(devices: Vec<Device>) -> Result<(), String> {
    write_devices_file(&devices)
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Region {
    pub id: String,
    pub name: String,
    pub lat_min: f64,
    pub lat_max: f64,
    pub lon_min: f64,
    pub lon_max: f64,
    pub zoom: u32,
    #[serde(default)]
    pub source: Option<String>,
    pub output_path: String,
    #[serde(default)]
    pub last_downloaded: Option<String>,
    #[serde(default)]
    pub tile_count: Option<u64>,
    #[serde(default)]
    pub gsd_m_per_px: Option<f64>,
    #[serde(default)]
    pub file_size_mb: Option<f64>,
    #[serde(default)]
    pub location_label: Option<String>,
}

fn regions_path() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("DroneVisionNav")
        .join("regions.json")
}

#[tauri::command]
pub fn load_regions() -> Result<Vec<Region>, String> {
    let path = regions_path();
    if !path.exists() {
        return Ok(vec![]);
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn save_regions(regions: Vec<Region>) -> Result<(), String> {
    let path = regions_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&regions).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())
}
