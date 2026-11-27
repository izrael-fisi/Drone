import { useEffect, useState } from "react";
import { siteRequest, SiteRequestError, type SiteSession } from "./SiteOperator";

export type SiteAssets = {
  tickets: { dependencyId: string; outageStart: string; url: string }[];
  commissioning: { version: number; sensorId: string; cameraId: string; boundaryId: string; createdAt: string; passed: boolean;
    checks?: { recording: boolean } }[];
  recordingChecks?: { version: number; cameraId: string; boundaryId: string; createdAt: string; recording: boolean }[];
  testNotificationsEnabled?: boolean;
  validatedRules?: { cameraId: string; ruleId: string; createdAt: string; actor: string; note: string }[];
};

export function useSiteAssets(session: SiteSession | undefined, version: number, active: boolean) {
  const [revision, setRevision] = useState(0);
  const [snapshot, setSnapshot] = useState<{ session: SiteSession; version: number; assets?: SiteAssets; error?: string }>();
  const current = snapshot?.session === session && snapshot?.version === version ? snapshot : undefined;

  useEffect(() => {
    if (!session || !version || !active) return;
    const connected = session;
    const lifetime = new AbortController();
    let timer: number;
    async function load() {
      try {
        const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(6000)]);
        const assets: SiteAssets = await siteRequest(connected, "assets", undefined, signal);
        if (!lifetime.signal.aborted) setSnapshot({ session: connected, version, assets });
      } catch (error: unknown) {
        if (lifetime.signal.aborted) return;
        const denied = error instanceof SiteRequestError && [401, 403, 404].includes(error.status);
        const message = error instanceof Error ? error.message : "Could not load site checks.";
        setSnapshot((previous) => ({ session: connected, version, error: message,
          assets: !denied && previous?.session === connected && previous.version === version ? previous.assets : undefined }));
        if (denied) return;
      }
      if (!lifetime.signal.aborted) timer = window.setTimeout(load, 15_000);
    }
    void load();
    return () => { lifetime.abort(); window.clearTimeout(timer); };
  }, [session, version, active, revision]);

  return { assets: current?.assets, error: current?.error, refresh: () => setRevision((value) => value + 1) };
}
