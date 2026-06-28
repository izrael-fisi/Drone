use reqwest::blocking::Client;
use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

#[derive(Serialize)]
struct HeartbeatRequest<'a> {
    endpoint: &'a str,
    timeout_s: f64,
}

#[derive(Serialize)]
struct QGroundControlLaunchRequest {
    stop_status_bridge: bool,
}

fn normalized_base_url(base_url: &str) -> Result<String, String> {
    let trimmed = base_url.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return Err("Edge API URL is empty".to_string());
    }
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        Ok(trimmed.to_string())
    } else {
        Ok(format!("http://{trimmed}"))
    }
}

fn client(timeout_s: u64) -> Result<Client, String> {
    Client::builder()
        .timeout(Duration::from_secs(timeout_s))
        .build()
        .map_err(|e| e.to_string())
}

fn get_json(base_url: String, path: &'static str, timeout_s: u64) -> Result<Value, String> {
    let url = format!("{}{}", normalized_base_url(&base_url)?, path);
    let response = client(timeout_s)?
        .get(url)
        .send()
        .map_err(|e| e.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("Edge API returned HTTP {status}"));
    }
    response.json::<Value>().map_err(|e| e.to_string())
}

fn post_json<T: Serialize>(
    base_url: String,
    path: &'static str,
    payload: &T,
    timeout_s: u64,
) -> Result<Value, String> {
    let url = format!("{}{}", normalized_base_url(&base_url)?, path);
    let response = client(timeout_s)?
        .post(url)
        .json(payload)
        .send()
        .map_err(|e| e.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("Edge API returned HTTP {status}"));
    }
    response.json::<Value>().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn edge_api_health(base_url: String) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || get_json(base_url, "/health", 3))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn edge_api_device(base_url: String) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || get_json(base_url, "/api/v1/device", 5))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn edge_api_status(base_url: String) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || get_json(base_url, "/api/v1/status", 5))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn edge_api_mavlink_heartbeat(
    base_url: String,
    endpoint: String,
    timeout_s: f64,
) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || {
        let url = format!("{}/api/v1/mavlink/heartbeat", normalized_base_url(&base_url)?);
        let response = client((timeout_s.ceil() as u64).saturating_add(3).max(5))?
            .post(url)
            .json(&HeartbeatRequest {
                endpoint: &endpoint,
                timeout_s,
            })
            .send()
            .map_err(|e| e.to_string())?;
        let status = response.status();
        if !status.is_success() {
            return Err(format!("Edge API returned HTTP {status}"));
        }
        response.json::<Value>().map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn edge_api_qgroundcontrol_status(base_url: String) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || get_json(base_url, "/api/v1/qgroundcontrol", 5))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn edge_api_qgroundcontrol_launch(
    base_url: String,
    stop_status_bridge: bool,
) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || {
        post_json(
            base_url,
            "/api/v1/qgroundcontrol/launch",
            &QGroundControlLaunchRequest { stop_status_bridge },
            10,
        )
    })
    .await
    .map_err(|e| e.to_string())?
}
