import { useState } from "react";
import type { Sensor } from "./monitoringModel";
import { gatewayStream } from "./gatewayStream";

export function CameraGatewayAccess({ camera }: { camera: Sensor }) {
  const stream = gatewayStream(camera);
  const [token, setToken] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [busy, setBusy] = useState(false);
  const [eventToken, setEventToken] = useState("");
  const [viewer, setViewer] = useState<{ url: string; expiresAt: number }>();
  const [error, setError] = useState("");
  if (!stream) return null;
  const base = stream.base;

  async function configure(action: "event-token" | "viewer-link", rotate = false) {
    if (!token.trim()) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(new URL("api/cameras/" + action, base), {
        method: "POST", redirect: "error", credentials: "omit", signal: AbortSignal.timeout(6000),
        headers: { Authorization: "Bearer " + token.trim(), "Content-Type": "application/json" },
        body: JSON.stringify({ deviceId: camera.id, minutes, rotate }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result?.error ?? "Gateway request failed.");
      if (action === "event-token" && /^[a-f0-9]{64}$/.test(result?.token)) setEventToken(result.token);
      else if (action === "viewer-link" && /^view\/[a-f0-9]{48}\/index\.m3u8$/.test(result?.streamPath) && Number.isFinite(result.expiresAt)) {
        const url = new URL("/camera-viewer", location.origin);
        url.hash = encodeURIComponent(new URL(result.streamPath, base).href);
        setViewer({ url: url.href, expiresAt: result.expiresAt });
      } else throw new Error("The gateway returned an invalid response.");
      setToken("");
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Gateway request failed."); }
    finally { setBusy(false); }
  }

  return <details className="gv-triage-section">
    <summary>Gateway access and analytics</summary>
    <div className="gv-triage-content gv-incident-form">
      <label>Gateway management token<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label>
      <label>Viewer link lifetime (minutes)<input type="number" min={1} max={30} step={1} value={minutes} onChange={(event) => setMinutes(Number(event.target.value))} /></label>
      <div className="gv-detail-actions">
        <button type="button" className="gv-button" disabled={busy || !token.trim() || !Number.isInteger(minutes) || minutes < 1 || minutes > 30}
          onClick={() => void configure("viewer-link")}>Create camera viewer link</button>
        <button type="button" className="gv-button" disabled={busy || !token.trim()} onClick={() => void configure("event-token")}>Get camera event token</button>
      </div>
      {viewer && <>
        <label>Viewer link<input readOnly value={viewer.url} onFocus={(event) => event.target.select()} /></label>
        <p role="status">Live video access expires {new Date(viewer.expiresAt * 1000).toLocaleString()}.</p>
      </>}
      {eventToken && <>
        <label>Analytics endpoint<input readOnly value={new URL(`api/cameras/${camera.id}/events`, base).href} onFocus={(event) => event.target.select()} /></label>
        <label>Camera event token<input readOnly value={eventToken} onFocus={(event) => event.target.select()} /></label>
      </>}
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
