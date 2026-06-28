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

export function networkHintLabel(hint: LocalNetworkHint) {
  return `${hint.interface_name} ${hint.ipv4} (${hint.network_hint})`;
}

export function networkHintKey(hint: LocalNetworkHint) {
  return `${hint.interface_name}|${hint.ipv4}|${hint.network_hint}`;
}

export function selectedNetworkHint(
  networkHints: LocalNetworkHint[],
  selectedKey: string,
) {
  return networkHints.find((hint) => networkHintKey(hint) === selectedKey) ?? networkHints[0] ?? null;
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

export function discoveryTroubleshooting(
  candidates: PiDiscoveryCandidate[],
  networkHints: LocalNetworkHint[],
  selectedHint?: LocalNetworkHint | null,
) {
  const reachable = candidates.some((candidate) => candidate.ssh_open);
  if (reachable) return [];
  const primaryNetwork = (selectedHint ?? networkHints[0])?.network_hint;
  const mdnsFailed = candidates.some(
    (candidate) => candidate.source === "mdns" && candidate.message.toLowerCase().includes("resolve"),
  );
  const resolvedButClosed = candidates.some((candidate) => candidate.resolved_ip && !candidate.ssh_open);
  return [
    primaryNetwork ? `Put the Pi on the same Wi-Fi subnet as this desktop: ${primaryNetwork}.` : "Connect this desktop and the Pi to the same Wi-Fi network.",
    "Enable SSH on the Pi and confirm it can run `hostname -I` locally.",
    "Try the Pi hostname `dronecompute.local` or the IP shown on the Pi login screen.",
    ...(mdnsFailed ? ["If `.local` names fail, use the Pi IP directly or enable Avahi/Bonjour/mDNS on the network."] : []),
    ...(resolvedButClosed ? ["If the host resolves but SSH is offline, check `sudo systemctl status ssh` and firewall/router isolation."] : []),
  ];
}

export function discoveryChecklistText({
  candidates,
  networkHints,
  selectedHint,
  targetHost,
  username,
}: {
  candidates: PiDiscoveryCandidate[];
  networkHints: LocalNetworkHint[];
  selectedHint?: LocalNetworkHint | null;
  targetHost?: string;
  username?: string;
}) {
  const host = targetHost?.trim() || "dronecompute.local";
  const user = username?.trim() || "user";
  const hint = selectedHint ?? networkHints[0] ?? null;
  const summary = discoveryStatusSummary(candidates, networkHints, hint);
  const candidateLines = candidates.slice(0, 8).map((candidate) => {
    const status = candidate.ssh_open ? "SSH open" : "SSH closed";
    return `- ${candidate.host}:${candidate.port} ${candidate.resolved_ip ? `(${candidate.resolved_ip}) ` : ""}${status}; source=${candidate.source}; ${candidate.message}`;
  });
  return [
    "Drone Vision Raspberry Pi discovery checklist",
    "",
    `Status: ${summary.label}`,
    `Detail: ${summary.detail}`,
    `Selected adapter: ${hint ? networkHintLabel(hint) : "none detected"}`,
    `Target host: ${host}`,
    "",
    "On the Pi:",
    "1. Confirm it is on the same Wi-Fi/VLAN as the desktop.",
    "2. Run: hostname && hostname -I",
    "3. Run: sudo systemctl enable --now ssh",
    "4. Run: sudo systemctl status ssh --no-pager",
    "5. If .local names should work, run: sudo systemctl enable --now avahi-daemon",
    "",
    "On the desktop:",
    `1. Test name resolution: ping -c 2 ${host}`,
    `2. Test SSH port: nc -vz ${host} 22`,
    `3. Test SSH login: ssh ${user}@${host}`,
    hint ? `4. Keep the desktop on adapter/subnet: ${networkHintLabel(hint)}` : "4. Select the Wi-Fi/Ethernet adapter connected to the Pi network.",
    "",
    "Network checks:",
    "- Disable guest-network/client-isolation between desktop and Pi.",
    "- Allow inbound TCP 22 on the Pi firewall.",
    "- If mDNS fails but the IP works, use the IP address in the app.",
    "- If SSH works in terminal but not the app, re-check username, key/password, and host key trust.",
    "",
    "Recent discovery candidates:",
    ...(candidateLines.length ? candidateLines : ["- none"]),
  ].join("\n");
}
