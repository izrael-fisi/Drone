import { useEffect } from "react";
import { fetchHealthReport, type HealthReport } from "./applianceHealth";

export function ApplianceHealthMonitor({ deviceId, url, interval, onReport }: {
  deviceId: string; url: string; interval: number; onReport: (report: HealthReport, url: string) => void;
}) {
  useEffect(() => {
    const session = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const report = await fetchHealthReport(url, deviceId, session.signal);
        if (!session.signal.aborted) onReport(report, url);
      } catch {
        // A failed request leaves the previous report to age according to its expected cadence.
      } finally {
        if (!session.signal.aborted) timer = window.setTimeout(poll, Math.min(interval, 60) * 1000);
      }
    }
    void poll();
    return () => {
      session.abort();
      window.clearTimeout(timer);
    };
  }, [deviceId, interval, onReport, url]);
  return null;
}
