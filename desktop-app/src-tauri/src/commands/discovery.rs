use serde::Serialize;
use std::collections::BTreeMap;
use std::io::Read;
use std::net::{Ipv4Addr, TcpStream, ToSocketAddrs};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const DEFAULT_PI_HOSTS: &[&str] = &[
    "dronecompute.local",
    "raspberrypi.local",
    "dronecompute",
    "raspberrypi",
];

#[derive(Serialize, Clone)]
pub struct PiDiscoveryCandidate {
    pub host: String,
    pub port: u16,
    pub source: String,
    pub ssh_open: bool,
    pub resolved_ip: Option<String>,
    pub ssh_banner: Option<String>,
    pub message: String,
    pub last_seen_unix_ms: u128,
}

#[derive(Serialize, Clone)]
pub struct LocalNetworkHint {
    pub interface_name: String,
    pub ipv4: String,
    pub network_hint: String,
    pub source: String,
    pub likely_active: bool,
}

#[tauri::command]
pub async fn discover_pi_devices(
    seed_hosts: Vec<String>,
    port: Option<u16>,
) -> Result<Vec<PiDiscoveryCandidate>, String> {
    let candidates = tokio::task::spawn_blocking(move || {
        let port = port.unwrap_or(22);
        let hosts = collect_candidate_hosts(&seed_hosts);
        let mut handles = Vec::new();
        for (host, source) in hosts.into_iter().take(48) {
            handles.push(std::thread::spawn(move || probe_host(host, port, source)));
        }

        let mut candidates = Vec::new();
        for handle in handles {
            if let Ok(candidate) = handle.join() {
                candidates.push(candidate);
            }
        }
        candidates.sort_by(|a, b| {
            b.ssh_open
                .cmp(&a.ssh_open)
                .then_with(|| source_rank(&a.source).cmp(&source_rank(&b.source)))
                .then_with(|| a.host.cmp(&b.host))
        });
        candidates
    })
    .await
    .map_err(|e| e.to_string())?;
    Ok(candidates)
}

#[tauri::command]
pub async fn local_network_hints() -> Result<Vec<LocalNetworkHint>, String> {
    tokio::task::spawn_blocking(collect_local_network_hints)
        .await
        .map_err(|e| e.to_string())
}

fn collect_candidate_hosts(seed_hosts: &[String]) -> Vec<(String, String)> {
    let mut hosts: BTreeMap<String, String> = BTreeMap::new();
    for host in seed_hosts {
        add_host(&mut hosts, host, "saved");
    }
    for host in DEFAULT_PI_HOSTS {
        add_host(&mut hosts, host, "mdns");
    }
    for ip in arp_neighbor_ips() {
        add_host(&mut hosts, &ip, "arp");
    }
    hosts.into_iter().collect()
}

fn collect_local_network_hints() -> Vec<LocalNetworkHint> {
    let mut hints = Vec::new();
    if let Ok(output) = Command::new("ip")
        .args(["-o", "-4", "addr", "show"])
        .output()
    {
        let text = String::from_utf8_lossy(&output.stdout);
        hints.extend(parse_ip_addr_hints(&text));
    }
    if let Ok(output) = Command::new("ifconfig").output() {
        let text = String::from_utf8_lossy(&output.stdout);
        hints.extend(parse_ifconfig_hints(&text));
    }
    if let Ok(output) = Command::new("ipconfig").output() {
        let text = String::from_utf8_lossy(&output.stdout);
        hints.extend(parse_ipconfig_hints(&text));
    }
    dedupe_network_hints(hints)
}

fn parse_ip_addr_hints(text: &str) -> Vec<LocalNetworkHint> {
    let mut hints = Vec::new();
    for line in text.lines() {
        let parts = line.split_whitespace().collect::<Vec<_>>();
        if parts.len() < 4 || parts[2] != "inet" {
            continue;
        }
        let interface_name = parts[1].trim_end_matches(':').to_string();
        let Some(ip_part) = parts.get(3) else {
            continue;
        };
        let ip = ip_part.split('/').next().unwrap_or("");
        if let Ok(ipv4) = ip.parse::<Ipv4Addr>() {
            if is_local_ipv4(ipv4) {
                hints.push(network_hint(interface_name, ipv4, "ip"));
            }
        }
    }
    hints
}

fn parse_ifconfig_hints(text: &str) -> Vec<LocalNetworkHint> {
    let mut hints = Vec::new();
    let mut interface_name = "unknown".to_string();
    for line in text.lines() {
        if !line.starts_with(char::is_whitespace) && line.contains(':') {
            interface_name = line
                .split(':')
                .next()
                .unwrap_or("unknown")
                .trim()
                .to_string();
        }
        let parts = line.split_whitespace().collect::<Vec<_>>();
        for window in parts.windows(2) {
            if window[0] == "inet" {
                if let Ok(ipv4) = window[1].parse::<Ipv4Addr>() {
                    if is_local_ipv4(ipv4) {
                        hints.push(network_hint(interface_name.clone(), ipv4, "ifconfig"));
                    }
                }
            }
        }
    }
    hints
}

fn parse_ipconfig_hints(text: &str) -> Vec<LocalNetworkHint> {
    let mut hints = Vec::new();
    let mut interface_name = "windows-adapter".to_string();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.ends_with(':') && !trimmed.contains("IPv4") {
            interface_name = trimmed.trim_end_matches(':').to_string();
        }
        if !trimmed.contains("IPv4") {
            continue;
        }
        if let Some(ip) = trimmed.rsplit(':').next() {
            if let Ok(ipv4) = ip.trim().parse::<Ipv4Addr>() {
                if is_local_ipv4(ipv4) {
                    hints.push(network_hint(interface_name.clone(), ipv4, "ipconfig"));
                }
            }
        }
    }
    hints
}

fn dedupe_network_hints(hints: Vec<LocalNetworkHint>) -> Vec<LocalNetworkHint> {
    let mut by_ip: BTreeMap<String, LocalNetworkHint> = BTreeMap::new();
    for hint in hints {
        by_ip.entry(hint.ipv4.clone()).or_insert(hint);
    }
    by_ip.into_values().collect()
}

fn network_hint(interface_name: String, ipv4: Ipv4Addr, source: &str) -> LocalNetworkHint {
    let octets = ipv4.octets();
    let network_hint = if ipv4.is_link_local() {
        format!("{}.{}.0.0/16", octets[0], octets[1])
    } else {
        format!("{}.{}.{}.0/24", octets[0], octets[1], octets[2])
    };
    LocalNetworkHint {
        interface_name,
        ipv4: ipv4.to_string(),
        network_hint,
        source: source.to_string(),
        likely_active: true,
    }
}

fn add_host(hosts: &mut BTreeMap<String, String>, host: &str, source: &str) {
    let host = host.trim().trim_end_matches('.');
    if host.is_empty() || host.contains(char::is_whitespace) {
        return;
    }
    hosts
        .entry(host.to_string())
        .or_insert_with(|| source.to_string());
}

fn source_rank(source: &str) -> u8 {
    match source {
        "saved" => 0,
        "mdns" => 1,
        "arp" => 2,
        _ => 3,
    }
}

fn now_unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn probe_host(host: String, port: u16, source: String) -> PiDiscoveryCandidate {
    let mut resolved_ip = None;
    let mut ssh_banner = None;
    let address = format!("{host}:{port}");
    let addrs = match address.to_socket_addrs() {
        Ok(addrs) => addrs.collect::<Vec<_>>(),
        Err(error) => {
            return PiDiscoveryCandidate {
                host,
                port,
                source,
                ssh_open: false,
                resolved_ip: None,
                ssh_banner: None,
                message: format!("Could not resolve host: {error}"),
                last_seen_unix_ms: now_unix_ms(),
            };
        }
    };
    let mut last_error = None;
    for addr in addrs.into_iter().take(4) {
        resolved_ip.get_or_insert_with(|| addr.ip().to_string());
        match TcpStream::connect_timeout(&addr, Duration::from_millis(650)) {
            Ok(mut stream) => {
                let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
                let mut buf = [0_u8; 96];
                if let Ok(n) = stream.read(&mut buf) {
                    if n > 0 {
                        let banner = String::from_utf8_lossy(&buf[..n]).trim().to_string();
                        if !banner.is_empty() {
                            ssh_banner = Some(banner);
                        }
                    }
                }
                return PiDiscoveryCandidate {
                    host,
                    port,
                    source,
                    ssh_open: true,
                    resolved_ip,
                    ssh_banner,
                    message: "SSH port reachable".to_string(),
                    last_seen_unix_ms: now_unix_ms(),
                };
            }
            Err(error) => {
                last_error = Some(error.to_string());
            }
        }
    }
    PiDiscoveryCandidate {
        host,
        port,
        source,
        ssh_open: false,
        resolved_ip,
        ssh_banner: None,
        message: last_error.unwrap_or_else(|| "No address candidates found".to_string()),
        last_seen_unix_ms: now_unix_ms(),
    }
}

fn arp_neighbor_ips() -> Vec<String> {
    let mut text = String::new();
    for (program, args) in [("arp", vec!["-a"]), ("ip", vec!["neigh", "show"])] {
        if let Ok(output) = Command::new(program).args(args).output() {
            text.push_str(&String::from_utf8_lossy(&output.stdout));
            text.push('\n');
            text.push_str(&String::from_utf8_lossy(&output.stderr));
            text.push('\n');
        }
    }
    extract_private_ipv4_candidates(&text)
}

fn extract_private_ipv4_candidates(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut token = String::new();
    for ch in text.chars().chain(std::iter::once(' ')) {
        if ch.is_ascii_digit() || ch == '.' {
            token.push(ch);
            continue;
        }
        if !token.is_empty() {
            if let Ok(ip) = token.parse::<Ipv4Addr>() {
                if is_local_ipv4(ip) {
                    let value = ip.to_string();
                    if !out.contains(&value) {
                        out.push(value);
                    }
                }
            }
            token.clear();
        }
    }
    out
}

fn is_local_ipv4(ip: Ipv4Addr) -> bool {
    ip.is_private() || ip.is_link_local()
}

#[cfg(test)]
mod tests {
    use super::{
        collect_candidate_hosts, extract_private_ipv4_candidates, parse_ifconfig_hints,
        parse_ip_addr_hints,
    };

    #[test]
    fn extracts_local_ipv4_addresses_from_neighbor_output() {
        let output = "? (192.168.1.158) at aa:bb on en0\n10.0.0.7 dev wlan0 lladdr cc REACHABLE\n8.8.8.8 dev en0";
        let ips = extract_private_ipv4_candidates(output);
        assert!(ips.contains(&"192.168.1.158".to_string()));
        assert!(ips.contains(&"10.0.0.7".to_string()));
        assert!(!ips.contains(&"8.8.8.8".to_string()));
    }

    #[test]
    fn collect_candidate_hosts_deduplicates_seeds() {
        let seeds = vec![
            "raspberrypi.local".to_string(),
            "dronecompute.local".to_string(),
        ];
        let hosts = collect_candidate_hosts(&seeds);
        let raspberry_count = hosts
            .iter()
            .filter(|(host, _)| host == "raspberrypi.local")
            .count();
        assert_eq!(raspberry_count, 1);
        assert!(hosts
            .iter()
            .any(|(host, source)| host == "dronecompute.local" && source == "saved"));
    }

    #[test]
    fn parses_linux_ip_addr_interface_hints() {
        let output = "2: wlan0    inet 192.168.1.157/24 brd 192.168.1.255 scope global wlan0\n1: lo    inet 127.0.0.1/8 scope host lo";
        let hints = parse_ip_addr_hints(output);
        assert_eq!(hints.len(), 1);
        assert_eq!(hints[0].interface_name, "wlan0");
        assert_eq!(hints[0].ipv4, "192.168.1.157");
        assert_eq!(hints[0].network_hint, "192.168.1.0/24");
    }

    #[test]
    fn parses_ifconfig_interface_hints() {
        let output = "en0: flags=8863<UP> mtu 1500\n\tinet 192.168.1.157 netmask 0xffffff00 broadcast 192.168.1.255\nlo0: flags=8049<UP> mtu 16384\n\tinet 127.0.0.1 netmask 0xff000000";
        let hints = parse_ifconfig_hints(output);
        assert_eq!(hints.len(), 1);
        assert_eq!(hints[0].interface_name, "en0");
        assert_eq!(hints[0].network_hint, "192.168.1.0/24");
    }
}
