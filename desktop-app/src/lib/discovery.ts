import type { LocalNetworkHint, PiDiscoveryCandidate } from "./types";

export const PI_DISCOVERY_HISTORY_KEY = "drone_pi_discovery_history";

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
    const key = `${candidate.host}:${candidate.port}`;
    const existing = byKey.get(key);
    if (!existing || candidate.last_seen_unix_ms > existing.last_seen_unix_ms || candidate.ssh_open) {
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

export function networkHintLabel(hint: LocalNetworkHint) {
  return `${hint.interface_name} ${hint.ipv4} (${hint.network_hint})`;
}

export function discoveryTroubleshooting(
  candidates: PiDiscoveryCandidate[],
  networkHints: LocalNetworkHint[],
) {
  const reachable = candidates.some((candidate) => candidate.ssh_open);
  if (reachable) return [];
  const primaryNetwork = networkHints[0]?.network_hint;
  return [
    primaryNetwork ? `Put the Pi on the same Wi-Fi subnet as this desktop: ${primaryNetwork}.` : "Connect this desktop and the Pi to the same Wi-Fi network.",
    "Enable SSH on the Pi and confirm it can run `hostname -I` locally.",
    "Try the Pi hostname `dronecompute.local` or the IP shown on the Pi login screen.",
  ];
}
