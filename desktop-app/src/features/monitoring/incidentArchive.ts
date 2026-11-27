import { useEffect, useState } from "react";
import type { IncomingObservation, Recording, Sensor } from "./monitoringModel";
import { gatewayStream } from "./gatewayStream";
import type { SiteSession } from "./SiteOperator";

export type ClipHold = { heldUntil: number; heldAt: number; heldBy: string; sha256: string };
export type IncidentArchive = { state: "loading" | "pending" | "missing" | "unsupported" | "error" }
  | { state: "available"; recording: Recording; clipId: string; gateway: string; cameraKey: string; hold?: ClipHold };

function readHold(value: unknown): ClipHold | undefined {
  if (!value || typeof value !== "object") return;
  const { heldUntil, heldAt, heldBy, sha256 } = value as Partial<ClipHold>;
  if (typeof heldUntil === "number" && Number.isFinite(heldUntil) && typeof heldAt === "number" && Number.isFinite(heldAt)
    && typeof heldBy === "string" && typeof sha256 === "string" && /^[0-9a-f]{64}$/.test(sha256)) return { heldUntil, heldAt, heldBy, sha256 };
}

export async function preserveClip(archive: Extract<IncidentArchive, { state: "available" }>, token: string, operator: string, enterprise = false) {
  const response = await fetch(new URL("api/clips/hold", archive.gateway), {
    method: "POST", redirect: "error", credentials: enterprise ? "same-origin" : "omit", signal: AbortSignal.timeout(6_000),
    headers: { "Content-Type": "application/json", ...(!enterprise && { Authorization: "Bearer " + token.trim() }) },
    body: JSON.stringify({ cameraKey: archive.cameraKey, clipId: archive.clipId, operator }),
  });
  const payload = await response.json();
  const hold = readHold(payload);
  if (!response.ok || !hold) throw new Error(typeof payload?.error === "string" ? payload.error : "Could not preserve the clip.");
  return hold;
}

export function recordingAccessToken(url: string, session?: SiteSession) {
  if (!session || session.enterprise || !session.token) return;
  const base = new URL("streams/", session.base);
  let target: URL;
  try { target = new URL(url, base); } catch { return; }
  if (target.origin === base.origin && !target.username && !target.password && target.pathname.startsWith(base.pathname)
      && /^[a-f0-9]{48}\/(recording\.json|clips\/[a-f0-9]{32}\.mp4)$/.test(target.pathname.slice(base.pathname.length))) {
    return session.token;
  }
}

export function useIncidentArchive(alert?: { observation: Pick<IncomingObservation, "recording" | "occurredAt"> }, camera?: Sensor, session?: SiteSession): IncidentArchive | undefined {
  const stream = !alert?.observation.recording ? gatewayStream(camera) : null;
  const gateway = stream?.base;
  const cameraKey = stream?.key;
  const occurredAt = alert?.observation.occurredAt;
  const cameraId = camera?.id;
  const requestId = gateway && cameraKey && occurredAt && cameraId ? JSON.stringify([gateway, cameraKey, occurredAt, cameraId]) : null;
  const [result, setResult] = useState<{ requestId: string; archive: IncidentArchive; session?: SiteSession }>();
  useEffect(() => {
    if (!gateway || !cameraKey || !occurredAt || !cameraId || !requestId) return;
    const base = gateway;
    const key = cameraKey;
    const controller = new AbortController();
    let timer: number;
    async function lookup(signal: AbortSignal, id: string, source: string, timestamp: string, attempt: number) {
      let archive: IncidentArchive = { state: "error" };
      try {
        const address = new URL(`streams/${key}/recording.json`, base);
        address.searchParams.set("at", String(Date.parse(timestamp)));
        const token = recordingAccessToken(address.href, session);
        const response = await fetch(address, { headers: token ? { Authorization: "Bearer " + token } : undefined, signal: AbortSignal.any([signal, AbortSignal.timeout(60_000)]),
          cache: "no-store", credentials: "same-origin", redirect: "error" });
        if (response.status === 404) archive = { state: "unsupported" };
        else if (response.ok) {
          const clip = await response.json();
          if (clip.state === "available" && typeof clip.clipId === "string" && /^[a-f0-9]{32}$/.test(clip.clipId)
            && Number.isFinite(clip.durationSeconds) && Number.isFinite(clip.eventOffsetSeconds)
            && clip.eventOffsetSeconds >= 0 && clip.eventOffsetSeconds < clip.durationSeconds) {
            archive = { state: "available", gateway: base, cameraKey: key, clipId: clip.clipId, hold: readHold(clip),
              recording: { cameraId: source, format: "mp4", eventOffsetSeconds: clip.eventOffsetSeconds,
                durationSeconds: clip.durationSeconds, url: new URL(`streams/${key}/clips/${clip.clipId}.mp4`, base).href } };
          } else if (["pending", "missing", "unsupported"].includes(clip.state)) archive = { state: clip.state };
        }
      } catch { /* The player reports an unavailable recorder. */ }
      if (signal.aborted) return;
      setResult({ requestId: id, archive, session });
      if ((archive.state === "pending" || archive.state === "missing") && attempt < 3) {
        timer = window.setTimeout(() => void lookup(signal, id, source, timestamp, attempt + 1), 5_000);
      }
    }
    void lookup(controller.signal, requestId, cameraId, occurredAt, 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [cameraId, cameraKey, gateway, occurredAt, requestId, session]);
  return requestId ? result?.requestId === requestId && result.session === session ? result.archive : { state: "loading" } : undefined;
}
