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
                } else if value.get("schema_version").and_then(Value::as_str)
                    == Some("vision_nav_runtime_status_v3")
                {
                    // The learned shadow runtime broadcasts its richer status packet. Normalize
                    // the position-facing portion so existing map/status panes consume it too.
                    let position = value.get("position").cloned().unwrap_or(Value::Null);
                    let active_map = value.get("active_map").cloned().unwrap_or(Value::Null);
                    let inference = value.get("inference").cloned().unwrap_or(Value::Null);
                    let mode = position
                        .get("navigation_mode")
                        .and_then(Value::as_str)
                        .unwrap_or("NO_GLOBAL_FIX");
                    latest = Some(serde_json::json!({
                        "schema_version": "vision_nav_position_update_v2",
                        "timestamp_utc": value.get("updated_at_utc").cloned(),
                        "sequence": value.get("sequence").cloned(),
                        "status": if mode == "TRACKING" { "accepted" } else { "unavailable" },
                        "source": "vision",
                        "source_state": position.get("source_state").cloned().unwrap_or(serde_json::json!("no_position")),
                        "source_transition_reason": value.pointer("/publication_decision/reason").cloned(),
                        "confidence": position.get("location_confidence").cloned(),
                        "vision_health": {
                            "available": mode == "TRACKING",
                            "status": mode,
                            "confidence": position.get("location_confidence").cloned(),
                            "tile_id": active_map.get("release_id").cloned(),
                        },
                        "learned_runtime": {
                            "profile_id": value.pointer("/active_model/profile_id").cloned(),
                            "model_release_id": value.pointer("/active_model/release_id").cloned(),
                            "provider": inference.get("provider").cloned(),
                            "provider_ready": inference.get("provider_ready").cloned(),
                            "navigation_mode": mode,
                            "shadow_mode": value.get("shadow_mode").cloned(),
                            "compatibility": value.get("compatibility").cloned(),
                        }
                    }));
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

    #[test]
    fn normalizes_learned_shadow_runtime_status() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .subsec_nanos();
        let port = 27000 + (stamp % 1000) as u16;
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            let socket = UdpSocket::bind(("127.0.0.1", 0)).expect("sender bind");
            socket
                .send_to(
                    br#"{"schema_version":"vision_nav_runtime_status_v3","sequence":4,"shadow_mode":true,"active_map":{"release_id":"map@2"},"active_model":{"profile_id":"pi5_crossview_nav_v1","release_id":"model@1"},"inference":{"provider":"onnxruntime_cpu","provider_ready":true},"position":{"source_state":"no_position","navigation_mode":"NO_GLOBAL_FIX","location_confidence":0.0},"publication_decision":{"reason":"awaiting_learned_inference_decision"}}"#,
                    ("127.0.0.1", port),
                )
                .expect("send shadow status");
        });
        let packet = receive_position_update_inner(port, 500)
            .expect("receive ok")
            .expect("packet");
        assert_eq!(packet["schema_version"], "vision_nav_position_update_v2");
        assert_eq!(packet["learned_runtime"]["profile_id"], "pi5_crossview_nav_v1");
        assert_eq!(packet["vision_health"]["status"], "NO_GLOBAL_FIX");
    }
}
