import { useEffect, useRef, useState } from "react";
import { httpEndpointUrl } from "./monitoringModel";

export type TelemetryConnection = { endpoint: string; token: string };
interface TelemetryStatus {
  state: "connecting" | "current" | "delayed" | "unreachable" | "unauthorized";
  backlogCount?: number; oldestQueuedAt?: number; skipped?: boolean; forwarding?: boolean;
}

export function useSiteTelemetry(connection: TelemetryConnection | undefined, onEvents: (events: unknown[]) => void) {
  const receive = useRef(onEvents);
  receive.current = onEvents;
  const [snapshot, setSnapshot] = useState<{ connection: TelemetryConnection; status: TelemetryStatus }>();
  useEffect(() => {
    if (!connection) return;
    const { endpoint, token } = connection;
    const controller = new AbortController();
    let timer: number;
    let after = 0;
    let skipped = false;
    async function poll() {
      let delay = 5_000;
      let status: TelemetryStatus = { state: "unreachable", skipped };
      try {
        const url = new URL(endpoint);
        url.searchParams.set("after", String(after));
        const response = await fetch(url, { headers: { Authorization: "Bearer " + token }, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(4000)]),
          redirect: "error", credentials: "same-origin", cache: "no-store" });
        if (response.status === 401 || response.status === 403) status = { state: "unauthorized", skipped };
        else if (response.ok) {
          const data = await response.json();
          if (!Array.isArray(data.events) || !Number.isSafeInteger(data.backlogCount) || data.backlogCount < 0) throw new Error("Invalid telemetry page");
          if (controller.signal.aborted) return;
          if (Number.isSafeInteger(data.firstSequence) && data.firstSequence > after + 1) skipped = true;
          if (data.events.length) {
            const events = data.events.slice(0, 200).filter((entry: { sequence?: unknown }) => entry && Number.isSafeInteger(entry.sequence) && Number(entry.sequence) > after);
            if (events.length) {
              receive.current(events);
              after = Math.max(after, ...events.map((entry: { sequence: number }) => entry.sequence));
            }
          }
          if (Number.isSafeInteger(data.after) && data.after >= after) after = data.after;
          if (data.hasMore || data.events.length >= 200) delay = 100;
          status = { state: data.backlogCount || delay === 100 ? "delayed" : "current", skipped, backlogCount: data.backlogCount,
            oldestQueuedAt: typeof data.oldestQueuedAt === "number" && Number.isFinite(data.oldestQueuedAt) ? data.oldestQueuedAt : undefined, forwarding: data.forwarding === true };
        }
      } catch { /* Keep current workspace data visible while the link recovers. */ }
      if (controller.signal.aborted) return;
      setSnapshot({ connection: connection!, status });
      timer = window.setTimeout(poll, delay);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [connection]);
  return snapshot?.connection === connection ? snapshot?.status : undefined;
}

export function readSiteEndpoint(raw: string) {
  const endpoint = httpEndpointUrl(raw);
  if (!endpoint) return;
  const url = new URL(endpoint);
  if (url.search || url.hash || url.protocol !== "https:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) return;
  return url.href.endsWith("/") ? url.href : url.href + "/";
}

export function SiteTelemetryPanel({ connection, status, onConnect, useOperator }: {
  connection?: TelemetryConnection; status?: TelemetryStatus; onConnect: (connection?: TelemetryConnection) => void; useOperator?: () => void;
}) {
  const [origin, setOrigin] = useState("http://127.0.0.1:8765");
  const [token, setToken] = useState("");
  const base = readSiteEndpoint(origin);
  const state = status?.state ?? "connecting";
  return <details className="gv-triage-section">
    <summary>Site telemetry connection</summary>
    <div className="gv-triage-content">
      {!connection ? <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault(); if (base && /^[a-f0-9]{64}$/.test(token.trim())) {
          onConnect({ endpoint: new URL("api/site/telemetry", base).href, token: token.trim() }); setToken("");
        }
      }}>
        <label>Site gateway URL<input type="url" required value={origin} onChange={(event) => setOrigin(event.target.value)} /></label>
        <label>Site telemetry reader token<input type="password" autoComplete="off" required value={token} onChange={(event) => setToken(event.target.value)} /></label>
        <button className="gv-button" type="submit" disabled={!base || !/^[a-f0-9]{64}$/.test(token.trim())}>Connect site telemetry</button>
        {useOperator && <button className="gv-button" type="button" onClick={useOperator}>Use signed-in operator for telemetry</button>}
      </form> : <>
        <p role="status">{{ connecting: "Connecting to site telemetry…", current: "Site telemetry is current.", delayed: "Site telemetry has a delivery backlog.",
          unreachable: "Connection interrupted. Showing the last received site data.", unauthorized: "The site telemetry token was rejected." }[state]}</p>
        {!!status?.backlogCount && <p>{status.backlogCount} events queued for upstream delivery{status.oldestQueuedAt ? ` · Oldest received ${new Date(status.oldestQueuedAt * 1000).toLocaleString()}` : ""}</p>}
        {status?.skipped && <p>Some earlier events predate the gateway’s retained history.</p>}
        {status && state === "current" && <p>{status.forwarding ? "Connected to the site gateway and its upstream queue." : "Connected to the local site gateway."}</p>}
        <button className="gv-button" type="button" onClick={() => onConnect()}>Disconnect site telemetry</button>
      </>}
    </div>
  </details>;
}
