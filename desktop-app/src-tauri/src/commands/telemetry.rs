use anyhow::{Context, Result};
use serde_json::Value;
use std::net::UdpSocket;
use std::time::Duration;

#[tauri::command]
pub async fn receive_position_update(port: u16, timeout_ms: u64) -> Result<Option<Value>, String> {
    tauri::async_runtime::spawn_blocking(move || receive_position_update_inner(port, timeout_ms))
        .await
        .map_err(|err| err.to_string())?
        .map_err(|err| err.to_string())
}

fn receive_position_update_inner(port: u16, timeout_ms: u64) -> Result<Option<Value>> {
    let socket =
        UdpSocket::bind(("0.0.0.0", port)).with_context(|| format!("bind UDP 0.0.0.0:{port}"))?;
    socket
        .set_read_timeout(Some(Duration::from_millis(timeout_ms.max(1))))
        .context("set UDP read timeout")?;

    let mut latest = None;
    let mut buf = [0_u8; 8192];
    loop {
        match socket.recv_from(&mut buf) {
            Ok((size, _addr)) => {
                let value: Value =
                    serde_json::from_slice(&buf[..size]).context("parse position update JSON")?;
                if matches!(
                    value.get("schema_version").and_then(Value::as_str),
                    Some("vision_nav_position_update_v1" | "vision_nav_position_update_v2")
                ) {
                    latest = Some(value);
                }
            }
            Err(err)
                if err.kind() == std::io::ErrorKind::WouldBlock
                    || err.kind() == std::io::ErrorKind::TimedOut =>
            {
                return Ok(latest);
            }
            Err(err) => return Err(err).context("receive UDP position update"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::UdpSocket;
    use std::thread;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn receives_position_update_packet() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .subsec_nanos();
        let port = 26000 + (stamp % 1000) as u16;
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            let socket = UdpSocket::bind(("127.0.0.1", 0)).expect("sender bind");
            socket
                .send_to(
                    br#"{"schema_version":"vision_nav_position_update_v2","source":"gps","source_state":"gps_primary","status":"accepted","lat_lon":{"lat":1.0,"lon":2.0}}"#,
                    ("127.0.0.1", port),
                )
                .expect("send position");
        });
        let packet = receive_position_update_inner(port, 500)
            .expect("receive ok")
            .expect("packet");
        assert_eq!(packet["source"], "gps");
        assert_eq!(packet["source_state"], "gps_primary");
    }
}
