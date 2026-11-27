import { useEffect, useRef, useState } from "react";
import type { Alert, Sensor } from "./monitoringModel";
import { readCoordinates } from "./configurationModel";
import { siteRequest, type SiteSession } from "./SiteOperator";

type Channel = { id: string; name: string; modality?: Sensor["modality"]; deviceId?: string };

export function XProtectCatalog({ session, onAdd, provider = "xprotect" }: {
  session: SiteSession; onAdd: (camera: Sensor) => void; provider?: "xprotect" | "genetec";
}) {
  const [channels, setChannels] = useState<Channel[]>();
  const [id, setId] = useState("");
  const [longitude, setLongitude] = useState("");
  const [latitude, setLatitude] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const position = readCoordinates({ longitude, latitude });
  const selected = channels?.find((channel) => channel.id === id);
  const label = provider === "genetec" ? "Genetec" : "XProtect";

  return <details className="gv-triage-section">
    <summary>{label} {provider === "genetec" ? "device catalog" : "camera catalog"}</summary>
    <div className="gv-triage-content gv-incident-form">
      <button type="button" className="gv-button" disabled={busy} onClick={async () => {
        setBusy(true); setStatus(""); setError("");
        try { const result = await siteRequest(session, "vms-channels?provider=" + provider); setChannels(result.channels); setStatus(`${result.channels.length} available channels.`); }
        catch (issue) { setError(issue instanceof Error ? issue.message : `Could not load ${label} channels.`); }
        finally { setBusy(false); }
      }}>Load {label} channels</button>
      {!!channels?.length && <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault(); if (!selected || !position) return;
        onAdd({ id: selected.deviceId ?? `xprotect-${selected.id.toLowerCase()}`, name: selected.name.slice(0, 60), position, modality: selected.modality ?? "vision", demo: false,
          groupId: null, labels: [], mapLabel: "", cameraView: null, cameraFeedUrl: null,
          cameraProvider: provider === "genetec" ? "Genetec Security Center" : "Milestone XProtect", cameraChannel: selected.id });
        setStatus(`${selected.name} added to the draft.`);
      }}>
        <label>{label} channel<select value={id} onChange={(event) => setId(event.target.value)}><option value="">Choose channel</option>
          {channels.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>Channel latitude<input required inputMode="decimal" value={latitude} onChange={(event) => setLatitude(event.target.value)} /></label>
        <label>Channel longitude<input required inputMode="decimal" value={longitude} onChange={(event) => setLongitude(event.target.value)} /></label>
        <button className="gv-button" type="submit" disabled={!selected || !position}>Add channel to site draft</button>
      </form>}
      {status && <p role="status">{status}</p>}{error && <p role="alert">{error}</p>}
    </div>
  </details>;
}

export function XProtectPlayback({ session, alert }: { session: SiteSession; alert: Alert }) {
  const video = useRef<HTMLVideoElement>(null);
  const [active, setActive] = useState(false);
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (!active || !video.current) return;
    const screen = video.current;
    const controller = new AbortController();
    let peer: RTCPeerConnection | undefined;
    let access: string | undefined;
    let expiry: number;
    let timer: number;
    const request = (body: Record<string, unknown>) => siteRequest(session, "vms-playback", body,
      AbortSignal.any([controller.signal, AbortSignal.timeout(8500)]));

    function stop() {
      controller.abort(); window.clearTimeout(expiry); window.clearTimeout(timer); peer?.close(); screen.srcObject = null;
      if (access) void siteRequest(session, "vms-playback", { operation: "close", session: access }).catch(() => {});
    }

    async function play() {
      setStatus("Connecting to the recorder…");
      try {
        const result = await request({ operation: "start", incidentId: alert.id });
        access = result.session;
        if (controller.signal.aborted) { stop(); return; }
        const iceServers: RTCIceServer[] = (Array.isArray(result.iceServers) ? result.iceServers : []).slice(0, 10)
          .filter((entry: { url?: string }) => typeof entry?.url === "string" && /^(stun|stuns|turn|turns):/.test(entry.url))
          .map((entry: { url: string; userName?: string; credential?: string }) => ({ urls: entry.url, username: entry.userName, credential: entry.credential }));
        const connection = peer = new RTCPeerConnection({ iceServers });
        connection.ontrack = (event) => {
          if (event.track.kind !== "video" || controller.signal.aborted) return;
          screen.srcObject = new MediaStream([event.track]);
          void screen.play().catch(() => {});
        };
        connection.onconnectionstatechange = () => {
          if (connection.connectionState === "failed" && !controller.signal.aborted) { setStatus("Recorder playback connection failed."); stop(); }
        };
        const local: string[] = [];
        connection.onicecandidate = ({ candidate }) => { if (candidate && local.length < 40) local.push(candidate.candidate); };
        await connection.setRemoteDescription({ type: "offer", sdp: result.offer });
        await connection.setLocalDescription(await connection.createAnswer());
        await new Promise<void>((resolve) => window.setTimeout(resolve, 1100));
        if (controller.signal.aborted) return;
        await request({ operation: "answer", session: access, answer: connection.localDescription?.sdp, candidates: local });
        const remote = new Set<string>();
        async function poll(attempt = 0) {
          if (controller.signal.aborted || connection.connectionState === "connected") return;
          try {
            const results = await request({ operation: "candidates", session: access });
            for (const candidate of results.candidates as string[]) {
              if (typeof candidate !== "string" || remote.has(candidate)) continue;
              remote.add(candidate);
              await connection.addIceCandidate({ candidate, sdpMLineIndex: 0 }).catch(() => {});
            }
            if (attempt >= 12) { setStatus("No recorded video arrived from XProtect."); stop(); }
            else timer = window.setTimeout(() => void poll(attempt + 1), 1000);
          } catch (issue) { if (!controller.signal.aborted) { setStatus(issue instanceof Error ? issue.message : "Recorder signaling failed."); stop(); } }
        }
        void poll();
        expiry = window.setTimeout(() => { setStatus("Recorder preview ended. Reopen playback to continue."); stop(); }, 65_000);
      } catch (issue) { if (!controller.signal.aborted) { setStatus(issue instanceof Error ? issue.message : "Could not play XProtect recording."); stop(); } }
    }
    void play();
    return stop;
  }, [active, alert.id, session]);

  return <div className="gv-incident-form">
    <button type="button" className="gv-button" aria-pressed={active} onClick={() => { setActive(!active); setStatus(""); }}>{active ? "Close XProtect playback" : "Play from XProtect recorder"}</button>
    {active && <><video className="gv-vms-video" ref={video} muted controls playsInline aria-label="XProtect recorded video" onPlaying={() => setStatus("Playing from XProtect recorder.")} />
      <p role="status">{status}</p></>}
  </div>;
}
