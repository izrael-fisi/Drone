import { useEffect, useRef } from "react";
import { parseObservation, type IncomingObservation, type Sensor } from "./monitoringModel";
import { gatewayStream } from "./gatewayStream";

export function useGatewayEvents(cameras: Sensor[], onEvents: (events: IncomingObservation[]) => void) {
  const receive = useRef(onEvents);
  receive.current = onEvents;
  const sources = JSON.stringify(cameras.flatMap((camera) => {
    const stream = gatewayStream(camera);
    return stream ? [[camera.id, new URL(`streams/${stream.key}/events.json`, stream.base).href]] : [];
  }));
  useEffect(() => {
    const cameras = JSON.parse(sources) as [string, string][];
    if (!cameras.length) return;
    const lifetime = new AbortController();
    const cursors = new Map<string, number>();
    let timer: number;
    async function poll() {
      const batches = await Promise.all(cameras.map(async ([id, endpoint]) => {
        try {
          const url = new URL(endpoint);
          const after = cursors.get(id) ?? 0;
          url.searchParams.set("after", String(after));
          const response = await fetch(url, { credentials: "same-origin", redirect: "error", cache: "no-store",
            signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(4000)]) });
          if (!response.ok) return [];
          const { events } = await response.json();
          if (!Array.isArray(events)) return [];
          const parsed = events.flatMap((event) => {
            if (!event || !Number.isSafeInteger(event.sequence) || event.sequence <= after) return [];
            try { return [parseObservation({ id: event.id, sensorId: id, summary: event.summary, occurredAt: event.occurredAt, eventType: event.event })]; }
            catch { return []; }
          });
          const next = Math.max(after, ...events.slice(0, 100).map((event) => Number.isSafeInteger(event?.sequence) ? event.sequence : 0));
          cursors.set(id, next);
          return parsed;
        } catch { return []; }
      }));
      if (lifetime.signal.aborted) return;
      const events = batches.flat().sort((a, b) => a.occurredAt.localeCompare(b.occurredAt));
      if (events.length) receive.current(events);
      timer = window.setTimeout(poll, 5000);
    }
    void poll();
    return () => { lifetime.abort(); window.clearTimeout(timer); };
  }, [sources]);
}
