import type { LocalNetworkHint, PiDiscoveryCandidate } from "./types";

const PI_DISCOVERY_HISTORY_KEY = "drone_pi_discovery_history";

export function loadDiscoveryHistory(): PiDiscoveryCandidate[] {
  try {
    const raw = localStorage.getItem(PI_DISCOVERY_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveDiscoveryHistory(candidates: PiDiscoveryCandidate[]) {
  localStorage.setItem(PI_DISCOVERY_HISTORY_KEY, JSON.stringify(candidates.slice(0, 12)));
}

export function mergeDiscoveryHistory(
  previous: PiDiscoveryCandidate[],
  incoming: PiDiscoveryCandidate[],
) {
  const byKey = new Map<string, PiDiscoveryCandidate>();
  for (const candidate of [...incoming, ...previous]) {
    const key = `${(candidate.resolved_ip || candidate.host).toLowerCase()}:${candidate.port}`;
    const existing = byKey.get(key);
    if (
      !existing
      || candidate.ssh_open && !existing.ssh_open
      || candidate.last_seen_unix_ms > existing.last_seen_unix_ms
    ) {
      byKey.set(key, candidate);
    }
  }
  return Array.from(byKey.values())
    .sort((a, b) => {
      if (a.ssh_open !== b.ssh_open) return a.ssh_open ? -1 : 1;
      return b.last_seen_unix_ms - a.last_seen_unix_ms;
    })
    .slice(0, 12);
}

export function candidateHost(candidate: PiDiscoveryCandidate) {
  return candidate.resolved_ip || candidate.host;
}

export function candidateName(candidate: PiDiscoveryCandidate) {
  const host = candidate.host.toLowerCase();
  if (host.includes("dronecompute")) return "dronecompute";
  if (host.includes("raspberry")) return "Raspberry Pi 5";
  return candidate.resolved_ip ? `Raspberry Pi ${candidate.resolved_ip}` : `Raspberry Pi ${candidate.host}`;
}

function networkHintLabel(hint: LocalNetworkHint) {
  return `${hint.interface_name} ${hint.ipv4} (${hint.network_hint})`;
}


export function discoveryStatusSummary(
  candidates: PiDiscoveryCandidate[],
  networkHints: LocalNetworkHint[],
  selectedHint?: LocalNetworkHint | null,
) {
  const reachable = candidates.filter((candidate) => candidate.ssh_open);
  const resolved = candidates.filter((candidate) => candidate.resolved_ip);
  const mdnsFailed = candidates.some(
    (candidate) => candidate.source === "mdns" && candidate.message.toLowerCase().includes("resolve"),
  );
  if (reachable.length > 0) {
    return {
      status: "ready" as const,
      label: `${reachable.length} SSH reachable`,
      detail: `Use ${candidateHost(reachable[0])} or keep scanning from ${selectedHint ? networkHintLabel(selectedHint) : "the active adapter"}.`,
    };
  }
  if (resolved.length > 0) {
    return {
      status: "blocked" as const,
      label: `${resolved.length} host resolved`,
      detail: "Host discovery worked, but TCP 22 is closed or filtered. Check SSH service and firewall rules on the Pi.",
    };
  }
  if (mdnsFailed) {
    return {
      status: "blocked" as const,
      label: "mDNS not resolving",
      detail: "Use the Pi IP address directly or enable Avahi/Bonjour on the local network.",
    };
  }
  return {
    status: "unknown" as const,
    label: "No Pi found",
    detail: networkHints.length > 0
      ? `Focus the selected adapter ${selectedHint ? networkHintLabel(selectedHint) : networkHintLabel(networkHints[0])}, then check Wi-Fi isolation and Pi SSH.`
      : "No private desktop network adapter was detected. Connect Wi-Fi/Ethernet, then scan again.",
  };
}

