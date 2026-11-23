import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { CameraFeed } from "./CameraFeed";
import { fetchHealthReport, getApplianceHealth, HEALTH_LABELS, isNewerReport, parseHealthReport, REPORT_INTERVAL, validReportInterval, type HealthReport } from "./applianceHealth";
import { readMonitoringFile } from "./monitoringModel";

export function ApplianceSetup({ deviceId, name, feed, gridFeed, interval, healthUrl, source, gatewayId, invalidGateway, existingReport, pendingReport, routeKey, canTestRoute, onInterval, onSource, onGateway, onReport, onTestRoute }: {
  deviceId: string; name: string; feed: string | null; gridFeed: string | null; interval: string;
  healthUrl: string; source: string | null; onSource: (url: string) => void;
  gatewayId: string; invalidGateway: boolean; onGateway: (id: string) => void;
  existingReport?: HealthReport; pendingReport?: HealthReport;
  routeKey: string; canTestRoute: boolean; onInterval: (value: string) => void;
  onReport: (report: HealthReport) => void; onTestRoute: () => string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const sequence = useRef(0);
  const [routeResult, setRouteResult] = useState<{ key: string; message: string }>();
  const [testedFeed, setTestedFeed] = useState<{ kind: "primary" | "grid"; url: string } | null>(null);
  const [reportError, setReportError] = useState("");
  const probe = useRef<AbortController>();
  const [probeResult, setProbeResult] = useState<{ source: string; state: "checking" | "passed" | "failed"; message: string }>();
  const result = probeResult?.source === source ? probeResult : undefined;
  const testingFeed = testedFeed && testedFeed.url === (testedFeed.kind === "primary" ? feed : gridFeed) ? testedFeed : null;
  const reportIntervalSeconds = Number(interval);
  const testHealth = pendingReport && getApplianceHealth({
    id: deviceId, demo: false, healthReport: pendingReport,
    reportIntervalSeconds: validReportInterval(reportIntervalSeconds) ? reportIntervalSeconds : REPORT_INTERVAL.default,
  }, Date.now());

  useEffect(() => () => { sequence.current++; }, []);
  useEffect(() => () => { probe.current?.abort(); }, [source]);

  async function importReport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const request = ++sequence.current;
    setReportError("");
    try {
      const report = parseHealthReport(await readMonitoringFile(file), Date.now());
      if (request !== sequence.current) return;
      if (report.sensorId !== deviceId) throw new Error("The report belongs to a different device.");
      if (!isNewerReport(report, pendingReport ?? existingReport)) throw new Error("A newer or equal report is already available.");
      onReport(report);
    } catch (error) {
      if (request === sequence.current) setReportError(error instanceof Error ? error.message : "Unable to read the health report.");
    }
  }

  async function testSource() {
    if (!source) return;
    probe.current?.abort();
    const controller = new AbortController();
    probe.current = controller;
    setProbeResult({ source, state: "checking", message: "Checking health source…" });
    try {
      const report = await fetchHealthReport(source, deviceId, controller.signal);
      if (controller.signal.aborted) return;
      if (isNewerReport(report, pendingReport ?? existingReport)) onReport(report);
      setProbeResult({ source, state: "passed", message: "Health source reached · Reported " + HEALTH_LABELS[report.state].toLowerCase() + "." });
    } catch (error) {
      if (!controller.signal.aborted) setProbeResult({ source, state: "failed",
        message: error instanceof Error ? error.message : "Unable to reach the health source." });
    }
  }

  return <fieldset className="gv-appliance-setup">
    <legend>Appliance setup</legend>
    <div className="gv-setup-id"><span>Device ID</span><code className="gv-mono">{deviceId}</code></div>
    <label>Expected report interval (seconds)<input type="number" required value={interval}
      min={REPORT_INTERVAL.min} max={REPORT_INTERVAL.max} step={1} onChange={(event) => onInterval(event.target.value)} /></label>
    <p className="gv-setup-note">Three missed reports mark a device as stopped reporting.</p>
    <label>Health source URL<input type="url" value={healthUrl} aria-invalid={!!healthUrl.trim() && !source}
      onChange={(event) => { setProbeResult(undefined); onSource(event.target.value); }}
      placeholder="https://gateway.example/devices/door/health" autoComplete="off" spellCheck={false} /></label>
    <p className="gv-setup-note">{healthUrl.trim() && !source ? "Use an HTTP or HTTPS URL without embedded credentials." : "Optional. Poll a site gateway for health reports while Global View is open."}</p>
    <label>Gateway ID<input value={gatewayId} maxLength={64} aria-invalid={invalidGateway} aria-describedby="gv-gateway-help"
      onChange={(event) => onGateway(event.target.value)} placeholder="e.g. east-gateway" autoComplete="off" spellCheck={false} /></label>
    <p id="gv-gateway-help" className="gv-setup-note">{invalidGateway
      ? "Start with a letter or number. Use letters, numbers, periods, hyphens, or underscores."
      : "Use the same ID on devices served by this gateway."}</p>
    <div className="gv-detail-actions">
      {source && <button type="button" className="gv-button" disabled={result?.state === "checking"} onClick={() => void testSource()}>Test health source</button>}
      <button type="button" className="gv-button" disabled={!canTestRoute} onClick={() => setRouteResult({ key: routeKey, message: onTestRoute() })}>Test alert route</button>
      <button type="button" className="gv-button" onClick={() => input.current?.click()}>Import test report</button>
      <input ref={input} hidden type="file" accept=".json,application/json" aria-label="Test report JSON file" onChange={importReport} />
      {feed && <button type="button" className="gv-button" onClick={() => setTestedFeed(testingFeed?.kind === "primary" ? null : { kind: "primary", url: feed })}>
        {testingFeed?.kind === "primary" ? "Close feed test" : "Test camera feed"}</button>}
      {gridFeed && <button type="button" className="gv-button" onClick={() => setTestedFeed(testingFeed?.kind === "grid" ? null : { kind: "grid", url: gridFeed })}>
        {testingFeed?.kind === "grid" ? "Close lower-bandwidth test" : "Test lower-bandwidth feed"}</button>}
    </div>
    {!canTestRoute && <p className="gv-setup-note">Place the device and assign an enabled boundary to test alert routing.</p>}
    {routeResult?.key === routeKey && canTestRoute && <p role="status">{routeResult.message}</p>}
    {reportError && <p role="alert">{reportError}</p>}
    {result && <p role={result.state === "failed" ? "alert" : "status"}>{result.message}</p>}
    {testHealth && <p role="status">Test report accepted · {HEALTH_LABELS[testHealth.status]}. It will be stored when you save.</p>}
    {testingFeed && <CameraFeed key={testingFeed.kind} camera={{ id: deviceId, name: name || "Draft camera", demo: false, cameraFeedUrl: testingFeed.url }} />}
  </fieldset>;
}
