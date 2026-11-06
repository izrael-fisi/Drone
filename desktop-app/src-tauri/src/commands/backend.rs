//! Backend connection and HTTP transport for the Proxigo platform.
//!
//! The desktop app talks to either Proxigo Cloud or an organization's
//! self-hosted (on-prem / private cloud) Proxigo server. Requests go through
//! Rust rather than the webview so that self-hosted servers work without CORS
//! configuration and can use a private certificate authority.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use super::secrets;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BackendKind {
    ProxigoCloud,
    SelfHosted,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct BackendConnection {
    /// Stable id; session tokens are stored per connection id.
    pub id: String,
    pub kind: BackendKind,
    pub label: String,
    /// Server root for self-hosted servers (discovery is read from
    /// `{base_url}/.well-known/proxigo`). Unused for Proxigo Cloud.
    #[serde(default)]
    pub base_url: Option<String>,
    /// PEM file of a private certificate authority to trust for this server.
    #[serde(default)]
    pub ca_cert_path: Option<String>,
}

impl BackendConnection {
    pub fn cloud() -> Self {
        Self {
            id: "proxigo-cloud".to_string(),
            kind: BackendKind::ProxigoCloud,
            label: "Proxigo Cloud".to_string(),
            base_url: None,
            ca_cert_path: None,
        }
    }
}

fn connection_path() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("DroneVisionNav")
        .join("backend.json")
}

/// The configured connection, or Proxigo Cloud when none is saved.
pub fn current_connection() -> BackendConnection {
    std::fs::read_to_string(connection_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_else(BackendConnection::cloud)
}

/// Trim, require http(s), drop a trailing slash. Returns the normalized URL.
pub fn normalize_base_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim().trim_end_matches('/');
    let parsed = reqwest::Url::parse(trimmed).map_err(|e| format!("Invalid server URL: {e}"))?;
    let host = parsed.host_str().unwrap_or_default();
    if host.is_empty() {
        return Err("Server URL needs a host name".to_string());
    }
    match parsed.scheme() {
        "https" => {}
        // Passwords and tokens would cross the network unencrypted; only a
        // server on this machine may use plain http.
        "http" if matches!(host, "localhost" | "127.0.0.1" | "[::1]") => {}
        "http" => return Err("Use https:// for organization servers. For a private certificate authority, add its CA certificate.".to_string()),
        other => return Err(format!("Server URL must start with https:// (got {other}://)")),
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err("Server URL must not contain ? or #".to_string());
    }
    Ok(trimmed.to_string())
}

fn self_hosted_id(base_url: &str) -> String {
    format!("self-hosted:{}", base_url.to_lowercase())
}

fn load_ca_certificate(path: &str) -> Result<reqwest::Certificate, String> {
    let pem = std::fs::read(path).map_err(|e| format!("Cannot read CA certificate {path}: {e}"))?;
    reqwest::Certificate::from_pem(&pem).map_err(|e| format!("Invalid CA certificate {path}: {e}"))
}

/// Remove stored session tokens for a connection.
pub fn clear_session_secrets(connection_id: &str) {
    secrets::delete_secret(&format!("{connection_id}:access_token"));
    secrets::delete_secret(&format!("{connection_id}:refresh_token"));
}

#[tauri::command]
pub fn backend_get_connection() -> Result<BackendConnection, String> {
    Ok(current_connection())
}

#[tauri::command]
pub fn backend_set_connection(connection: BackendConnection) -> Result<BackendConnection, String> {
    let next = match connection.kind {
        BackendKind::ProxigoCloud => BackendConnection::cloud(),
        BackendKind::SelfHosted => {
            let base_url = normalize_base_url(connection.base_url.as_deref().unwrap_or_default())?;
            let ca_cert_path = connection
                .ca_cert_path
                .map(|p| p.trim().to_string())
                .filter(|p| !p.is_empty());
            if let Some(path) = &ca_cert_path {
                load_ca_certificate(path)?;
            }
            let label = connection.label.trim();
            BackendConnection {
                id: self_hosted_id(&base_url),
                kind: BackendKind::SelfHosted,
                label: if label.is_empty() { base_url.clone() } else { label.to_string() },
                base_url: Some(base_url),
                ca_cert_path,
            }
        }
    };

    let previous = current_connection();
    if previous.id != next.id {
        // Tokens are only valid for the server that issued them.
        clear_session_secrets(&previous.id);
    }

    let path = connection_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&next).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(next)
}

#[derive(Deserialize)]
pub struct HttpRequest {
    pub method: String,
    pub url: String,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub body: Option<String>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// Apply the backend connection's TLS settings (private CA).
    #[serde(default)]
    pub backend_tls: bool,
}

#[derive(Serialize)]
pub struct HttpResponse {
    pub status: u16,
    pub ok: bool,
    /// Final URL after redirects.
    pub url: String,
    pub content_type: Option<String>,
    pub body: String,
}

const DEFAULT_TIMEOUT_MS: u64 = 20_000;
const MAX_TIMEOUT_MS: u64 = 120_000;

/// Perform an HTTP(S) request for the webview. Network failures are returned
/// as `Err("network: ...")`; HTTP error statuses are returned as responses.
#[tauri::command]
pub async fn http_request(request: HttpRequest) -> Result<HttpResponse, String> {
    let url = reqwest::Url::parse(&request.url).map_err(|e| format!("network: invalid URL: {e}"))?;
    if !matches!(url.scheme(), "https" | "http") {
        return Err("network: only http(s) URLs are allowed".to_string());
    }
    let method = reqwest::Method::from_bytes(request.method.to_uppercase().as_bytes())
        .map_err(|_| format!("network: invalid method {}", request.method))?;
    let timeout = Duration::from_millis(request.timeout_ms.unwrap_or(DEFAULT_TIMEOUT_MS).min(MAX_TIMEOUT_MS));

    let mut builder = reqwest::Client::builder()
        .timeout(timeout)
        .user_agent(concat!("Proxigo-Desktop/", env!("CARGO_PKG_VERSION")));
    if request.backend_tls {
        if let Some(path) = current_connection().ca_cert_path {
            builder = builder.add_root_certificate(load_ca_certificate(&path).map_err(|e| format!("network: {e}"))?);
        }
    }
    let client = builder.build().map_err(|e| format!("network: {e}"))?;

    let mut outgoing = client.request(method, url);
    for (name, value) in &request.headers {
        outgoing = outgoing.header(name, value);
    }
    if let Some(body) = request.body {
        outgoing = outgoing.body(body);
    }

    let response = outgoing.send().await.map_err(|e| format!("network: {e}"))?;
    let status = response.status();
    let final_url = response.url().to_string();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let body = response.text().await.map_err(|e| format!("network: reading response failed: {e}"))?;
    Ok(HttpResponse {
        status: status.as_u16(),
        ok: status.is_success(),
        url: final_url,
        content_type,
        body,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_base_urls() {
        assert_eq!(normalize_base_url(" https://proxigo.acme.local/ ").unwrap(), "https://proxigo.acme.local");
        assert_eq!(normalize_base_url("https://10.0.0.5:8443/proxigo").unwrap(), "https://10.0.0.5:8443/proxigo");
        assert_eq!(normalize_base_url("http://localhost:8080").unwrap(), "http://localhost:8080");
        assert!(normalize_base_url("http://10.0.0.5:8443").is_err());
        assert!(normalize_base_url("ftp://host").is_err());
        assert!(normalize_base_url("not a url").is_err());
        assert!(normalize_base_url("https://host/?x=1").is_err());
    }

    #[test]
    fn self_hosted_ids_are_case_insensitive() {
        assert_eq!(self_hosted_id("https://Proxigo.Acme.local"), "self-hosted:https://proxigo.acme.local");
    }
}
