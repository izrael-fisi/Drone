use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use ssh2::Session;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter};

#[derive(Serialize)]
pub struct CommandResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Deserialize, Clone)]
#[serde(tag = "type")]
pub enum SshAuth {
    Password {
        password: String,
    },
    Key {
        key_path: String,
        #[serde(default)]
        passphrase: Option<String>,
    },
}

#[derive(Serialize, Clone)]
pub struct UploadProgress {
    pub file: String,
    pub bytes_sent: u64,
    pub total_bytes: u64,
    pub percent: f32,
}

#[derive(Serialize)]
pub struct DownloadFileResult {
    pub remote_path: String,
    pub local_path: String,
    pub bytes_received: u64,
}

#[derive(Serialize)]
pub struct TestConnectionResult {
    pub ok: bool,
    pub message: String,
    pub server_banner: Option<String>,
    pub fingerprint: Option<String>,
}

#[derive(Serialize)]
pub struct CameraFrameResult {
    pub mime_type: String,
    pub base64_data: String,
    pub remote_path: String,
    pub stdout: String,
    pub stderr: String,
}

fn connect_session(host: &str, port: u16, username: &str, auth: &SshAuth) -> Result<Session> {
    let addr = format!("{host}:{port}");
    let tcp = TcpStream::connect(&addr).with_context(|| format!("Cannot reach {addr}"))?;
    tcp.set_read_timeout(Some(std::time::Duration::from_secs(30)))?;

    let mut sess = Session::new()?;
    sess.set_tcp_stream(tcp);
    sess.handshake().context("SSH handshake failed")?;

    match auth {
        SshAuth::Password { password } => {
            sess.userauth_password(username, password)
                .context("Password authentication failed")?;
        }
        SshAuth::Key {
            key_path,
            passphrase,
        } => {
            let key = Path::new(key_path);
            sess.userauth_pubkey_file(username, None, key, passphrase.as_deref())
                .context("Key authentication failed")?;
        }
    }

    if !sess.authenticated() {
        return Err(anyhow!("Authentication failed"));
    }
    Ok(sess)
}

#[tauri::command]
pub async fn test_ssh_connection(
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
) -> Result<TestConnectionResult, String> {
    tokio::task::spawn_blocking(
        move || match connect_session(&host, port, &username, &auth) {
            Ok(sess) => {
                let banner = sess.banner().map(|s| s.to_string());
                let fingerprint = sess.host_key_hash(ssh2::HashType::Sha256).map(|bytes| {
                    use base64::Engine;
                    format!(
                        "SHA256:{}",
                        base64::engine::general_purpose::STANDARD_NO_PAD.encode(bytes)
                    )
                });
                Ok(TestConnectionResult {
                    ok: true,
                    message: format!("Connected to {host}:{port} as {username}"),
                    server_banner: banner,
                    fingerprint,
                })
            }
            Err(e) => Ok(TestConnectionResult {
                ok: false,
                message: e.to_string(),
                server_banner: None,
                fingerprint: None,
            }),
        },
    )
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

#[tauri::command]
pub async fn ssh_run_command(
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    command: String,
) -> Result<CommandResult, String> {
    tokio::task::spawn_blocking(move || {
        let sess = connect_session(&host, port, &username, &auth).map_err(|e| e.to_string())?;
        let mut channel = sess.channel_session().map_err(|e| e.to_string())?;
        channel.exec(&command).map_err(|e| e.to_string())?;

        let mut stdout = String::new();
        let mut stderr = String::new();
        channel.read_to_string(&mut stdout).ok();
        channel.stderr().read_to_string(&mut stderr).ok();
        channel.wait_close().ok();
        let exit_code = channel.exit_status().unwrap_or(-1);

        Ok(CommandResult {
            exit_code,
            stdout,
            stderr,
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

fn scp_send_file(sess: &Session, local: &Path, remote: &str) -> Result<u64> {
    let metadata = std::fs::metadata(local)?;
    let size = metadata.len();
    let mut local_file = std::fs::File::open(local)?;
    let mut channel = sess.scp_send(Path::new(remote), 0o644, size, None)?;

    let mut buf = [0u8; 65536];
    let mut sent = 0u64;
    loop {
        let n = local_file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        channel.write_all(&buf[..n])?;
        sent += n as u64;
    }
    channel.send_eof()?;
    channel.wait_eof()?;
    channel.close()?;
    channel.wait_close()?;
    Ok(sent)
}

fn ensure_remote_dir(sess: &Session, remote_dir: &str) -> Result<()> {
    let mut channel = sess.channel_session()?;
    let cmd = format!("mkdir -p {}", shell_quote(remote_dir));
    channel.exec(&cmd)?;
    channel.wait_close()?;
    Ok(())
}

#[tauri::command]
pub async fn ssh_upload_files(
    app: AppHandle,
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    local_paths: Vec<String>,
    remote_dir: String,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        inner_upload(
            &app,
            &host,
            port,
            &username,
            &auth,
            &local_paths,
            &remote_dir,
        )
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

#[tauri::command]
pub async fn ssh_upload_directory(
    app: AppHandle,
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    local_dir: String,
    remote_dir: String,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        inner_upload_directory(&app, &host, port, &username, &auth, &local_dir, &remote_dir)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

#[tauri::command]
pub async fn ssh_upload_project(
    app: AppHandle,
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    local_dir: String,
    remote_dir: String,
) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        inner_upload_project(&app, &host, port, &username, &auth, &local_dir, &remote_dir)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

#[tauri::command]
pub async fn ssh_download_file(
    app: AppHandle,
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    remote_path: String,
    local_dir: String,
) -> Result<DownloadFileResult, String> {
    tokio::task::spawn_blocking(move || {
        inner_download_file(
            &app,
            &host,
            port,
            &username,
            &auth,
            &remote_path,
            &local_dir,
        )
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

#[tauri::command]
pub async fn ssh_capture_camera_frame(
    host: String,
    port: u16,
    username: String,
    auth: SshAuth,
    remote_project_path: String,
    width: u32,
    height: u32,
    timeout_ms: u32,
) -> Result<CameraFrameResult, String> {
    tokio::task::spawn_blocking(move || {
        inner_capture_camera_frame(
            &host,
            port,
            &username,
            &auth,
            &remote_project_path,
            width,
            height,
            timeout_ms,
        )
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e: anyhow::Error| e.to_string())
}

fn inner_download_file(
    app: &AppHandle,
    host: &str,
    port: u16,
    username: &str,
    auth: &SshAuth,
    remote_path: &str,
    local_dir: &str,
) -> Result<DownloadFileResult> {
    let sess = connect_session(host, port, username, auth)?;
    let filename = Path::new(remote_path)
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| anyhow!("Remote path has no filename: {remote_path}"))?
        .to_string();
    let local_root = expand_local_path(local_dir)?;
    std::fs::create_dir_all(&local_root)
        .with_context(|| format!("Cannot create {}", local_root.display()))?;
    let local_path = local_root.join(&filename);

    let (mut remote_file, stat) = sess.scp_recv(Path::new(remote_path))?;
    let total = stat.size();
    let _ = app.emit(
        "download-progress",
        UploadProgress {
            file: filename.clone(),
            bytes_sent: 0,
            total_bytes: total,
            percent: 0.0,
        },
    );

    let mut output = std::fs::File::create(&local_path)?;
    let mut buf = [0u8; 65536];
    let mut received = 0u64;
    loop {
        let n = remote_file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        output.write_all(&buf[..n])?;
        received += n as u64;
        let percent = if total == 0 {
            100.0
        } else {
            ((received as f64 / total as f64) * 100.0).min(100.0) as f32
        };
        let _ = app.emit(
            "download-progress",
            UploadProgress {
                file: filename.clone(),
                bytes_sent: received,
                total_bytes: total,
                percent,
            },
        );
    }
    let _ = app.emit(
        "download-progress",
        UploadProgress {
            file: filename,
            bytes_sent: received,
            total_bytes: total,
            percent: 100.0,
        },
    );
    Ok(DownloadFileResult {
        remote_path: remote_path.to_string(),
        local_path: local_path.to_string_lossy().into_owned(),
        bytes_received: received,
    })
}

fn inner_upload(
    app: &AppHandle,
    host: &str,
    port: u16,
    username: &str,
    auth: &SshAuth,
    local_paths: &[String],
    remote_dir: &str,
) -> Result<()> {
    let sess = connect_session(host, port, username, auth)?;
    ensure_remote_dir(&sess, remote_dir)?;

    for local_str in local_paths {
        let local = Path::new(local_str);
        let filename = local.file_name().and_then(|n| n.to_str()).unwrap_or("file");
        let remote_path = format!("{remote_dir}/{filename}");
        let total = std::fs::metadata(local)?.len();

        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: filename.to_string(),
                bytes_sent: 0,
                total_bytes: total,
                percent: 0.0,
            },
        );

        let sent = scp_send_file(&sess, local, &remote_path)?;

        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: filename.to_string(),
                bytes_sent: sent,
                total_bytes: total,
                percent: 100.0,
            },
        );
    }
    Ok(())
}

fn inner_upload_directory(
    app: &AppHandle,
    host: &str,
    port: u16,
    username: &str,
    auth: &SshAuth,
    local_dir: &str,
    remote_dir: &str,
) -> Result<()> {
    let sess = connect_session(host, port, username, auth)?;
    let root = Path::new(local_dir);
    if !root.is_dir() {
        return Err(anyhow!(
            "Local bundle directory not found: {}",
            root.display()
        ));
    }
    ensure_remote_dir(&sess, remote_dir)?;

    for file in collect_files(root)? {
        let rel = file
            .strip_prefix(root)?
            .to_string_lossy()
            .replace('\\', "/");
        let remote_path = format!("{}/{}", remote_dir.trim_end_matches('/'), rel);
        if let Some(parent) = Path::new(&remote_path).parent().and_then(|p| p.to_str()) {
            ensure_remote_dir(&sess, parent)?;
        }
        let total = std::fs::metadata(&file)?.len();
        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: rel.clone(),
                bytes_sent: 0,
                total_bytes: total,
                percent: 0.0,
            },
        );
        let sent = scp_send_file(&sess, &file, &remote_path)?;
        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: rel,
                bytes_sent: sent,
                total_bytes: total,
                percent: 100.0,
            },
        );
    }
    Ok(())
}

fn inner_upload_project(
    app: &AppHandle,
    host: &str,
    port: u16,
    username: &str,
    auth: &SshAuth,
    local_dir: &str,
    remote_dir: &str,
) -> Result<()> {
    let sess = connect_session(host, port, username, auth)?;
    let root = Path::new(local_dir);
    if !root.is_dir() {
        return Err(anyhow!(
            "Local project directory not found: {}",
            root.display()
        ));
    }
    ensure_remote_dir(&sess, remote_dir)?;

    for file in collect_project_files(root)? {
        let rel = file
            .strip_prefix(root)?
            .to_string_lossy()
            .replace('\\', "/");
        let remote_path = format!("{}/{}", remote_dir.trim_end_matches('/'), rel);
        if let Some(parent) = Path::new(&remote_path).parent().and_then(|p| p.to_str()) {
            ensure_remote_dir(&sess, parent)?;
        }
        let total = std::fs::metadata(&file)?.len();
        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: rel.clone(),
                bytes_sent: 0,
                total_bytes: total,
                percent: 0.0,
            },
        );
        let sent = scp_send_file(&sess, &file, &remote_path)?;
        let _ = app.emit(
            "upload-progress",
            UploadProgress {
                file: rel,
                bytes_sent: sent,
                total_bytes: total,
                percent: 100.0,
            },
        );
    }
    Ok(())
}

fn inner_capture_camera_frame(
    host: &str,
    port: u16,
    username: &str,
    auth: &SshAuth,
    remote_project_path: &str,
    width: u32,
    height: u32,
    timeout_ms: u32,
) -> Result<CameraFrameResult> {
    let sess = connect_session(host, port, username, auth)?;
    let remote_path = format!(
        "/home/{}/DroneTransfer/outgoing/camera-preview/latest.jpg",
        username
    );
    let command = format!(
        "cd {repo} && mkdir -p \"$HOME/DroneTransfer/outgoing/camera-preview\" && \
if [ -x \"$HOME/drone_vision_nav_venv/bin/python\" ]; then \
  PYTHONPATH=src \"$HOME/drone_vision_nav_venv/bin/python\" -m vision_nav.capture_frame --output {out} --width {width} --height {height} --timeout-ms {timeout_ms}; \
elif command -v rpicam-still >/dev/null 2>&1; then \
  rpicam-still --nopreview --timeout {timeout_ms} --width {width} --height {height} -o {out}; \
elif command -v libcamera-still >/dev/null 2>&1; then \
  libcamera-still --nopreview --timeout {timeout_ms} --width {width} --height {height} -o {out}; \
else \
  echo 'No supported camera capture tool found.' >&2; exit 127; \
fi",
        repo = shell_quote(remote_project_path),
        out = shell_quote(&remote_path),
        width = width,
        height = height,
        timeout_ms = timeout_ms,
    );

    let mut channel = sess.channel_session()?;
    channel.exec(&command)?;
    let mut stdout = String::new();
    let mut stderr = String::new();
    channel.read_to_string(&mut stdout).ok();
    channel.stderr().read_to_string(&mut stderr).ok();
    channel.wait_close().ok();
    let exit_code = channel.exit_status().unwrap_or(-1);
    if exit_code != 0 {
        return Err(anyhow!(
            "Camera capture failed with exit code {exit_code}.\n{stdout}\n{stderr}"
        ));
    }

    let (mut remote_file, _) = sess.scp_recv(Path::new(&remote_path))?;
    let mut bytes = Vec::new();
    remote_file.read_to_end(&mut bytes)?;
    use base64::Engine;
    Ok(CameraFrameResult {
        mime_type: "image/jpeg".to_string(),
        base64_data: base64::engine::general_purpose::STANDARD.encode(bytes),
        remote_path,
        stdout,
        stderr,
    })
}

fn collect_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    collect_files_into(root, &mut files)?;
    files.sort();
    Ok(files)
}

fn collect_files_into(path: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    for entry in std::fs::read_dir(path)? {
        let entry = entry?;
        let p = entry.path();
        if p.is_dir() {
            collect_files_into(&p, files)?;
        } else if p.is_file() {
            files.push(p);
        }
    }
    Ok(())
}

fn collect_project_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    collect_project_files_into(root, root, &mut files)?;
    files.sort();
    Ok(files)
}

fn collect_project_files_into(root: &Path, path: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    for entry in std::fs::read_dir(path)? {
        let entry = entry?;
        let p = entry.path();
        if should_skip_project_path(root, &p) {
            continue;
        }
        if p.is_dir() {
            collect_project_files_into(root, &p, files)?;
        } else if p.is_file() {
            files.push(p);
        }
    }
    Ok(())
}

fn should_skip_project_path(root: &Path, path: &Path) -> bool {
    let rel = path.strip_prefix(root).unwrap_or(path);
    let first = rel
        .components()
        .next()
        .and_then(|component| component.as_os_str().to_str());
    if matches!(
        first,
        Some(".git")
            | Some(".agents")
            | Some(".codex")
            | Some("desktop-app")
            | Some("data")
            | Some("logs")
            | Some("map_bundles")
            | Some("transfer")
    ) {
        return true;
    }
    rel.components().any(|component| {
        matches!(
            component.as_os_str().to_str(),
            Some("__pycache__")
                | Some(".pytest_cache")
                | Some(".mypy_cache")
                | Some(".ruff_cache")
                | Some(".npm-cache")
                | Some("node_modules")
                | Some("target")
                | Some(".DS_Store")
        )
    })
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn expand_local_path(path: &str) -> Result<PathBuf> {
    if path.is_empty() {
        return Err(anyhow!("Local download directory is empty"));
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
    fn expands_home_prefixed_download_paths() {
        let expanded =
            expand_local_path("~/DroneTransfer/from-pi/support-bundles").expect("expand home path");
        assert!(expanded.ends_with("DroneTransfer/from-pi/support-bundles"));
    }
}
