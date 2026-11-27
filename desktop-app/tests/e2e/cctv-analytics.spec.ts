import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { execFile, execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { createReadStream } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer, request } from "node:http";
import { createServer as createTlsServer } from "node:https";
import { connect as connectSocket, createServer as reservePort, type Socket } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { localInferenceFixture } from "./fixtures/local-inference";
import { onvifEventsFixture } from "./fixtures/onvif-events";

const gatewayScript = fileURLToPath(new URL("../../../services/camera-gateway/gateway.py", import.meta.url));
const workerScript = fileURLToPath(new URL("../../../services/camera-gateway/analytics_worker.py", import.meta.url));
const recorderClip = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const stairReference = fileURLToPath(new URL("../../../tests/fixtures/cctv-twilight-g639-stairs.json", import.meta.url));
const dockReference = fileURLToPath(new URL("../../../tests/fixtures/cctv-industry-g336-dock.json", import.meta.url));
const python = process.env.PROXIGO_CCTV_PYTHON, ground = process.env.PROXIGO_CCTV_YOLOX, drone = process.env.PROXIGO_CCTV_OWLV2;
const yolo11 = process.env.PROXIGO_CCTV_YOLO11, siteVideo = process.env.PROXIGO_CCTV_LOAD_VIDEO;
const yolo11Provider = process.env.PROXIGO_CCTV_YOLO11_PROVIDER ?? "CPUExecutionProvider";
const groundImage = process.env.PROXIGO_CCTV_GROUND_IMAGE, droneImage = process.env.PROXIGO_CCTV_DRONE_IMAGE;
const mediaMtx = process.env.MEDIAMTX;
const runProcess = promisify(execFile);
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
test.use({ actionTimeout: 8000 });

function spawnReady(child: ChildProcess, pattern: RegExp) {
  return new Promise<RegExpMatchArray>((resolve, reject) => {
    let output = "";
    const timer = setTimeout(fail, 12_000);
    function cleanup() { clearTimeout(timer); child.stdout?.off("data", read); child.stderr?.off("data", read); child.off("exit", fail); }
    function fail() { cleanup(); reject(new Error(`Fixture failed to start. ${output}`)); }
    function read(chunk: Buffer) {
      output += chunk.toString();
      const match = output.match(pattern);
      if (match) { cleanup(); resolve(match); }
    }
    child.stdout?.on("data", read); child.stderr?.on("data", read); child.once("exit", fail);
  });
}

function overlapsReferenceBox(box: unknown, reference: number[]) {
  if (!Array.isArray(box) || box.length !== 4) return false;
  const [left, top, right, bottom] = box;
  const [a, b, c, d] = reference;
  const intersection = Math.max(0, Math.min(right, c) - Math.max(left, a)) * Math.max(0, Math.min(bottom, d) - Math.max(top, b));
  const union = (right - left) * (bottom - top) + (c - a) * (d - b) - intersection;
  return intersection / union >= .30;
}

function publisherClock(child: ChildProcess) {
  let pending = "";
  const offsets: number[] = [];
  child.stdout?.on("data", (chunk: Buffer) => {
    const lines = (pending + chunk.toString()).split("\n");
    pending = lines.pop() ?? "";
    for (const line of lines) {
      const match = /^out_time_us=(\d+)\r?$/.exec(line);
      if (match) {
        offsets.push(Date.now() - Number(match[1]) / 1000);
        if (offsets.length > 16) offsets.shift();
      }
    }
  });
  return () => {
    expect(offsets.length).toBeGreaterThanOrEqual(4);
    const sorted = [...offsets].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  };
}

type ReplayObservation = {
  sensorId: string; occurredAt: string; eventType: string;
  analytics?: { dwellSeconds?: number; box?: unknown; source?: string; model?: string; ruleId?: string; trackId?: string };
};

function matchesDoorwayDwell(observation: ReplayObservation) {
  // G301 person box at the dwell threshold from the source annotation.
  return observation.eventType === "zone-dwell" && (observation.analytics?.dwellSeconds ?? 0) >= 15 &&
    overlapsReferenceBox(observation.analytics?.box, [.65625, .417593, .683333, .526852]);
}

type StairMonitoring = {
  analyticsWorkers: Record<string, { reportedAt: number; fps: number; state: string; models: string[] }>;
  siteEvents: { observation?: ReplayObservation }[];
};

async function verifyStairCrossings(testInfo: TestInfo, clocks: Map<string, () => number>,
  siteMonitoring: () => Promise<StairMonitoring>, modelMatches: (camera: string, model: string | undefined) => boolean) {
  const period = 55_000, after = Date.now();
  const sources = [...clocks].map(([id, clock]) => {
    const epoch = clock();
    const start = epoch + Math.ceil((after - epoch) / period) * period;
    return { id, start, epoch, clock, rates: new Map<number, number>() };
  });
  const lastStart = Math.max(...sources.map(({ start }) => start));
  let status: StairMonitoring = { analyticsWorkers: {}, siteEvents: [] };
  await expect.poll(async () => {
    status = await siteMonitoring();
    if (Date.now() >= lastStart + 10_000) {
      for (const { id, rates } of sources) {
        const worker = status.analyticsWorkers[id];
        if (worker?.state === "running" && modelMatches(id, worker.models[0])) rates.set(worker.reportedAt, worker.fps);
      }
    }
    return Date.now();
  }, { timeout: 2 * period + 6_000, intervals: [1000] }).toBeGreaterThan(lastStart + period + 2000);
  // The source segment starts at 26 seconds. Widen the reviewed bands for RTSP delivery timing.
  const bands = [[1.5, 5.75, 2], [10.5, 13.5, 1], [43.5, 46.5, 1], [46.5, 49.5, 1], [49.5, 53.25, 1]];
  const report = sources.map(({ id, start, epoch, clock, rates }) => {
    const crossings = status.siteEvents.flatMap(({ observation }) => {
      if (observation?.sensorId !== id || observation.analytics?.source !== "live") return [];
      const seconds = (Date.parse(observation.occurredAt) - start) / 1000;
      if (seconds < 0 || seconds >= period / 1000) return [];
      return [{ seconds, track: observation.analytics.trackId,
        valid: observation.eventType === "line-crossing" && observation.analytics.ruleId === "stairs" &&
          modelMatches(id, observation.analytics.model) }];
    });
    const counts = bands.map(([lo, hi]) => crossings.filter(({ seconds, valid }) => valid && lo <= seconds && seconds < hi).length);
    const uniqueTracks = new Set(crossings.flatMap(({ track }) => track ? [track] : []));
    return { id, crossings, counts, uniqueTracks: uniqueTracks.size, clockShift: clock() - epoch, rates: [...rates.values()] };
  });
  await testInfo.attach("Stair crossings and throughput per camera", {
    body: JSON.stringify(report, null, 2), contentType: "application/json",
  });
  expect.soft(report.filter(({ clockShift }) => Math.abs(clockShift) > 1000)).toEqual([]);
  expect.soft(report.filter(({ rates }) => rates.length < 2 || rates.some((rate) => rate < 3.5))).toEqual([]);
  expect(report.filter(({ crossings, counts, uniqueTracks }) =>
    crossings.length !== 6 || uniqueTracks !== 6 || counts.some((count, index) => count !== bands[index][2]))).toEqual([]);
}

async function stop(child: ChildProcess) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  const done = once(child, "exit");
  child.kill();
  const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
  try { await done; } finally { clearTimeout(timer); }
}

test("cloud and on-premises CCTV workers produce incidents and follow published compute placement", async ({ page, baseURL }) => {
  test.skip(![python, yolo11 ?? ground, drone, groundImage, droneImage, mediaMtx].every(Boolean), "Configure the local analytics models, two fixture images, and MediaMTX.");
  test.setTimeout(180_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-cctv-fixture-"));
  const children: ChildProcess[] = [];
  const start = (bin: string, args: string[], env = process.env) => {
    const child = spawn(bin, args, { env, stdio: ["ignore", "pipe", "pipe"] });
    children.push(child);
    return child;
  };
  const admin = "1".repeat(64), groundToken = "2".repeat(64), droneToken = "3".repeat(64);
  const viewerToken = "4".repeat(64), yardKey = "a".repeat(48);
  const localGroundToken = "5".repeat(64), legacyGroundToken = "6".repeat(64);
  const adminHeaders = { "Content-Type": "application/json", Authorization: "Bearer " + admin };
  let gatewayPort = "", drops = 0, reachable = false;
  const archive = join(directory, "recording.mkv");
  const key = join(directory, "localhost.key"), cert = join(directory, "localhost.pem");
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1", "-subj", "/CN=localhost",
    "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", cert], { stdio: "ignore" });
  let recording: Buffer;
  const recorder = createServer((incoming, outgoing) => {
    const url = new URL(incoming.url ?? "/", "http://127.0.0.1");
    if (url.pathname.endsWith("/list.cgi")) {
      const start = Date.parse(url.searchParams.get("starttime") ?? "");
      outgoing.end(`<root><recording recordingid="demo" diskid="SD" starttime="${new Date(start).toISOString()}" stoptime="${new Date(start + 4000).toISOString()}"/></root>`);
    } else if (url.pathname.endsWith("/exportrecording.cgi")) outgoing.end(recording);
    else outgoing.writeHead(404).end();
  });
  const connectivity = createTlsServer({ key: await readFile(key), cert: await readFile(cert) }, (incoming, outgoing) => {
    if (incoming.method === "POST" && incoming.url === "/api/site/telemetry" && !reachable) {
      if (incoming.headers.authorization === "Bearer " + groundToken) drops++;
      incoming.resume();
      outgoing.writeHead(503).end();
      return;
    }
    const proxied = request({ host: "127.0.0.1", port: gatewayPort, method: incoming.method, path: incoming.url, headers: incoming.headers }, (response) => {
      outgoing.writeHead(response.statusCode!, response.headers);
      response.pipe(outgoing);
    });
    proxied.on("error", () => outgoing.writeHead(502).end());
    incoming.pipe(proxied);
  });
  const cloudClients = new Set<Socket>();
  let rtspPort = 0;
  const cloudVideo = reservePort((client) => {
    const camera = connectSocket(rtspPort, "127.0.0.1");
    cloudClients.add(client);
    client.on("error", () => client.destroy()).on("close", () => { cloudClients.delete(client); camera.destroy(); });
    camera.on("error", () => camera.destroy()).on("close", () => client.destroy());
    client.pipe(camera).pipe(client);
  });
  try {
    execFileSync("ffmpeg", ["-v", "error", "-i", recorderClip, "-c", "copy", archive]);
    recording = await readFile(archive);
    recorder.listen(0, "127.0.0.1"); await once(recorder, "listening");
    const recorderAddress = "http://127.0.0.1:" + (recorder.address() as { port: number }).port;
    const reserve = reservePort();
    reserve.listen(0, "127.0.0.1"); await once(reserve, "listening");
    rtspPort = (reserve.address() as { port: number }).port;
    await new Promise<void>((resolve) => reserve.close(() => resolve()));
    const mt = join(directory, "mediamtx.yml");
    await writeFile(mt, `rtspAddress: 127.0.0.1:${rtspPort}\nrtspTransports: [tcp]\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  all_others:\n`);
    await spawnReady(start(mediaMtx!, [mt]), /\[RTSP\].*(?:started|listener opened)/);
    const rtsp = `rtsp://127.0.0.1:${rtspPort}`;
    cloudVideo.listen(0, "127.0.0.1"); await once(cloudVideo, "listening");
    const cloudRtsp = "rtsp://127.0.0.1:" + (cloudVideo.address() as { port: number }).port;
    async function publish(file: string, path: string, dimensions: string) {
      const clip = join(directory, path + ".mp4");
      await runProcess("ffmpeg", ["-v", "error", "-loop", "1", "-framerate", "4", "-i", file, "-frames:v", "8",
        "-vf", "scale=" + dimensions, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-g", "8", "-pix_fmt", "yuv420p", clip]);
      await spawnReady(start("ffmpeg", ["-re", "-stream_loop", "-1", "-i", clip, "-c", "copy",
        "-f", "rtsp", "-rtsp_transport", "tcp", rtsp + "/" + path]), /Output #0, rtsp/);
    }
    await Promise.all([publish(groundImage!, "yard", "640:960"), publish(droneImage!, "sky", "768:512")]);
    const access = join(directory, "access.json"), tokens = join(directory, "devices.json"), workers = join(directory, "workers.json"),
      state = join(directory, "gateway");
    await mkdir(state);
    await writeFile(join(state, "cameras.json"), JSON.stringify({ "yard-camera": { key: yardKey, url: rtsp + "/yard" } }));
    const timeZone = "America/Los_Angeles";
    await writeFile(access, JSON.stringify({ site: "CCTV pilot", siteId: "cctv-site", timeZone, organization: { id: "cctv-pilot", name: "CCTV pilot" },
      operators: { admin: { name: "Pilot Supervisor", role: "org-admin", cameras: "*", tokenSha256: createHash("sha256").update(admin).digest("hex") },
        viewer: { name: "Sky Viewer", role: "viewer", cameras: ["sky-camera"], tokenSha256: createHash("sha256").update(viewerToken).digest("hex") } } }));
    await writeFile(tokens, JSON.stringify({ "yard-camera": legacyGroundToken }));
    await writeFile(workers, JSON.stringify({ "yard-camera": { cloud: groundToken, "on-premises": localGroundToken },
      "sky-camera": { "on-premises": droneToken } }));
    gatewayPort = (await spawnReady(start("python3", [gatewayScript, "--port", "0", "--state-dir", state, "--allow-source", "127.0.0.1/32",
      "--site-access", access, "--telemetry-devices", tokens, "--analytics-workers", workers, "--axis-recorder", recorderAddress]), /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
    const gateway = `http://127.0.0.1:${gatewayPort}`;
    connectivity.listen(0, "127.0.0.1"); await once(connectivity, "listening");
    const uplink = "https://127.0.0.1:" + (connectivity.address() as { port: number }).port;
    const devices = [["yard", "Yard CCTV"], ["sky", "Sky CCTV"]] as const;
    const update = (payload: object) => fetch(gateway + "/api/site/configuration", { method: "POST", headers: adminHeaders, body: JSON.stringify(payload) });
    const seed = await update({ operation: "save", publishedVersion: 0, draftRevision: 0, configuration: {
      sensors: devices.map(([id, name]) => ({ id: id + "-camera", name, modality: "vision", position: [-122.3, 37.78],
        ...(id === "yard" ? { cameraGatewayKind: "rtsp", cameraFeedUrl: `${gateway}/streams/${yardKey}/index.m3u8` } : {}) })),
      boundaries: devices.map(([id]) => ({ id, name: id === "yard" ? "Loading yard" : "Sky observation sector", kind: "Restricted", enabled: true,
        sensorIds: [id + "-camera"], vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] })),
    } });
    expect(seed.status, await seed.clone().text()).toBe(200);
    expect((await update({ operation: "publish", publishedVersion: 0, draftRevision: (await seed.json()).draftRevision, message: "CCTV pilot devices" })).status).toBe(200);

    await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
    await page.goto(baseURL!);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    const posture = page.getByRole("region", { name: "Posture", exact: true });
    const inventory = page.getByRole("region", { name: "Devices", exact: true });
    const inspector = page.getByRole("complementary", { name: "Monitor and review" });
    async function connect() {
      await button(rail, "Posture").click();
      await posture.locator("summary", { hasText: "Shared site operator access" }).click();
      await posture.getByLabel("Shared site gateway URL").fill(gateway);
      await posture.getByLabel("Site operator token").fill(admin);
      await button(posture, "Connect operator").click();
      await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
    }
    await connect();
    const versionSummary = posture.getByText("Site configuration and versions", { exact: true });
    if (await versionSummary.locator("..").getAttribute("open") === null) await versionSummary.click();
    await button(posture, "Edit published configuration as a draft").click();

    async function editRule(device: string, name: string, behavior: string, mode: string, requireMovement = false) {
      await inspector.locator(".gv-cctv-editor > summary").click();
      const compute = inspector.getByLabel("Analytics compute");
      await expect(compute).toHaveValue("cloud");
      if (device === "Sky CCTV") await compute.selectOption("on-premises");
      await button(inspector, "Add CCTV rule").click();
      const details = inspector.locator(".gv-cctv-editor > div > details").last();
      await details.locator(":scope > summary").click();
      await details.getByLabel("Rule name").fill(name);
      await details.getByLabel("Behavior").selectOption(behavior);
      await details.getByLabel("Rule mode").selectOption(mode);
      if (behavior === "restricted-stop") {
        const movement = details.getByLabel("Require movement before the stop");
        await expect(movement).not.toBeChecked();
        if (requireMovement) await movement.check();
      }
      if (behavior === "zone-dwell") {
        await details.getByLabel("Time in zone (seconds)").fill("2");
        await details.getByLabel("Maximum detection gap (seconds)").fill("0.5");
      } else await details.getByLabel("Confirmation time (seconds)").fill("1");
      async function setPolygon(polygon: number[][]) {
        for (const [index, [x, y]] of polygon.entries()) {
          await details.getByLabel(`Point ${index + 1} X`).fill(String(x));
          await details.getByLabel(`Point ${index + 1} Y`).fill(String(y));
        }
      }
      await setPolygon([[0, 0], [1, 0], [1, 1], [0, 1]]);
      if (name === "Yard entry") {
        const format = new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
        const now = Date.now();
        await details.getByLabel("Use day and night profiles").check();
        await expect(details.getByText(/Profile times use America\/Los_Angeles/)).toBeVisible();
        await details.getByLabel("Day starts").fill(format.format(now - 30 * 60_000));
        await details.getByLabel("Night starts").fill(format.format(now + 30 * 60_000));
        await details.getByLabel("Night · Minimum confidence (%)", { exact: true }).fill("99");
        await button(details, "Add exclusion zone").click();
        await button(details, "Remove exclusion zone").click();
        await expect(details.getByLabel("Image shape")).toHaveValue("-1");
        await button(details, "Add exclusion zone").click();
        await setPolygon([[.2, .80], [.32, .80], [.32, 1], [.2, 1]]);
        await details.getByLabel("Image shape").selectOption("-1");
        await expect(details.getByLabel("Point 3 X")).toHaveValue("1");
      }
      await expect(details.getByLabel("Alert boundary")).not.toHaveValue("");
      await button(inspector, "Save device").click();
      await expect(inspector.getByRole("heading", { name: device, exact: true })).toBeVisible();
    }

    async function editCamera(device: string) {
      await button(rail, "Devices").click();
      await inventory.getByRole("button", { name: new RegExp("^" + device) }).click();
      await button(inspector, "Edit device").click();
    }

    await editCamera("Yard CCTV");
    await editRule("Yard CCTV", "Yard entry", "zone-entry", "observe");
    await editCamera("Yard CCTV");
    await editRule("Yard CCTV", "No stopping", "restricted-stop", "alert");
    await editCamera("Yard CCTV");
    await editRule("Yard CCTV", "Observed vehicle stop", "restricted-stop", "observe", true);
    await editCamera("Yard CCTV");
    await editRule("Yard CCTV", "Time in yard", "zone-dwell", "alert");
    await editCamera("Sky CCTV");
    await editRule("Sky CCTV", "Sky watch", "drone-sighting", "alert");
    await button(page, "Draft mode · Review changes").click();
    await button(posture, "Save site draft").click();
    await expect(posture.getByText("Shared site draft saved.")).toBeVisible();
    await posture.getByLabel("Version note").fill("Versioned CCTV rules");
    await button(posture, "Publish saved site draft").click();
    await expect(page.getByText("Version 2", { exact: true })).toBeVisible();
    const policy = await fetch(gateway + "/api/cameras/yard-camera/analytics", {
      headers: { Authorization: "Bearer " + groundToken },
    }).then((response) => response.json());
    expect(policy.timeZone).toBe(timeZone);
    expect(policy).toMatchObject({ placement: "cloud", organizationId: "cctv-pilot", siteId: "cctv-site", cameraId: "yard-camera" });
    const workerRequest = (camera: string, token: string, path = `/api/cameras/${camera}/analytics`, body?: object) =>
      fetch(gateway + path, { headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
        ...(body && { method: "POST", body: JSON.stringify(body) }) });
    expect((await workerRequest("yard-camera", localGroundToken)).status).toBe(403);
    expect((await workerRequest("yard-camera", legacyGroundToken)).status).toBe(403);
    expect(policy.rules).toEqual(expect.arrayContaining([expect.objectContaining({
      name: "Yard entry", schedule: expect.objectContaining({ night: expect.objectContaining({ minimumScore: .99 }) }),
      exclusions: [[[.2, .8], [.32, .8], [.32, 1], [.2, 1]]],
    }), expect.objectContaining({ name: "Time in yard", seconds: 2, dwellGapSeconds: .5 }),
    expect.objectContaining({ name: "Observed vehicle stop", requireMovement: true })]));
    await posture.getByText("CCTV analytics", { exact: true }).click();
    await expect(posture.getByText(/Waiting for cloud worker/)).toBeVisible();
    await expect(posture.getByText(/Waiting for on-premises worker/)).toBeVisible();

    async function workerConfig(id: string, token: string, lane: string, model: object, width: number, height: number, placement: "cloud" | "on-premises") {
      const worker = id + "-" + placement;
      const tokenFile = join(directory, worker + ".token"), config = join(directory, worker + ".json");
      await writeFile(tokenFile, token);
      await writeFile(config, JSON.stringify({ gateway: placement === "cloud" ? uplink : gateway, placement, siteId: "cctv-site", organizationId: "cctv-pilot",
        tokenFile, source: (placement === "cloud" ? cloudRtsp : rtsp) + "/" + id, sensorId: id + "-camera",
        state: join(directory, worker + "-outbox.sqlite3"), width, height, fps: 4, models: { [lane]: model } }));
      return config;
    }
    const workerEnv = { ...process.env, SSL_CERT_FILE: cert };
    async function startWorker(...args: Parameters<typeof workerConfig>) {
      const config = await workerConfig(...args);
      const child = start(python!, [workerScript, "--config", config], workerEnv);
      child.stderr?.on("data", (data: Buffer) => process.stderr.write(data));
      return child;
    }
    const yardModel = yolo11
      ? { kind: "yolo11", path: yolo11, fps: 4, sha256: createHash("sha256").update(await readFile(yolo11)).digest("hex"),
        personTrackSupport: true, provider: yolo11Provider }
      : { kind: "yolox", path: ground, fps: 4 };
    const checkConfig = await workerConfig("yard", groundToken, "ground", yardModel, 640, 960, "cloud");
    const wrongSite = join(directory, "wrong-site.json");
    await writeFile(wrongSite, JSON.stringify({ ...JSON.parse(await readFile(checkConfig, "utf8")), siteId: "other-site" }));
    const check = (config: string) => runProcess(python!, [workerScript, "--config", config, "--check-connection"],
      { env: workerEnv, timeout: 20_000 });
    await expect(check(wrongSite)).rejects.toMatchObject({ stderr: expect.stringContaining("different organization, site, or camera") });
    expect(cloudClients.size).toBe(0);
    const statusCheck = { type: "analytics-link-check", sensorId: "yard-camera", policyVersion: 2 };
    expect((await workerRequest("yard-camera", localGroundToken, "/api/site/telemetry", statusCheck)).status).toBe(403);
    expect((await workerRequest("yard-camera", groundToken, "/api/site/telemetry", { ...statusCheck, policyVersion: 1 })).status).toBe(422);
    reachable = true;
    expect(JSON.parse((await check(checkConfig)).stdout)).toMatchObject({
      connected: true, cameraId: "yard-camera", policyVersion: 2, placement: "cloud", videoFrameDecoded: true, telemetryVerified: true,
    });
    reachable = false;
    const emptyTelemetry = await fetch(gateway + "/api/site/telemetry", { headers: adminHeaders }).then((response) => response.json());
    expect(emptyTelemetry.events).toEqual([]);
    await expect(posture.getByText(/Waiting for cloud worker/)).toBeVisible();
    const yardWorker = await startWorker("yard", groundToken, "ground", yardModel, 640, 960, "cloud");
    await expect.poll(() => drops, { timeout: 26_000 }).toBeGreaterThan(0);
    const before = await fetch(gateway + "/api/site/monitoring", { headers: adminHeaders }).then((response) => response.json());
    expect(before.alerts).toHaveLength(0);
    await stop(yardWorker);
    await page.goto("about:blank");
    reachable = true;
    await startWorker("yard", groundToken, "ground", yardModel, 640, 960, "cloud");
    await startWorker("sky", droneToken, "drone", {
      kind: "owlv2", path: drone, fps: .3, device: process.env.PROXIGO_CCTV_OWLV2_DEVICE ?? "cpu",
    }, 768, 512, "on-premises");
    await expect.poll(async () => {
      const status = await fetch(gateway + "/api/site/monitoring", { headers: adminHeaders }).then((response) => response.json());
      return status.alerts.some((alert: { observation: { eventType: string } }) => alert.observation.eventType === "restricted-stop");
    }, { timeout: 12_000 }).toBe(true);
    const monitored = await fetch(gateway + "/api/site/monitoring", { headers: adminHeaders }).then((response) => response.json());
    expect(monitored.siteEvents.some((row: { observation: { analytics?: { ruleName?: string } } }) =>
      row.observation.analytics?.ruleName === "Observed vehicle stop")).toBe(false);
    await page.goto(baseURL!);
    await connect();
    await posture.getByText("CCTV analytics", { exact: true }).click();
    await expect(posture.getByText(/Person entered zone · Yard entry/).first()).toBeVisible({ timeout: 18_000 });
    await expect(posture.getByText(/Analyzing video/).first()).toBeVisible({ timeout: 20_000 });
    const review = posture.getByRole("region", { name: "Observed CCTV detections" });
    await review.getByLabel("Current published version").check();
    await review.getByRole("button", { name: /^Person entered zone · Yard entry/ }).first().click();
    const observation = review.getByRole("region", { name: "Selected CCTV observation" });
    await expect(observation).toContainText("Version 2");
    await button(observation, "Review recording").click();
    await expect(observation.locator("video")).toBeVisible({ timeout: 22_000 });
    await expect.poll(() => observation.locator("video").evaluate((element: HTMLVideoElement) => element.videoWidth), { timeout: 12_000 }).toBeGreaterThan(0);
    await observation.getByLabel("Review decision").selectOption("confirmed");
    await observation.getByLabel("Review note").fill("Verified against the yard recording.");
    await button(observation, "Save detection review").click();
    await expect(review.getByText("Detection review saved.")).toBeVisible();
    await page.goto(baseURL!);
    await connect();
    await posture.getByText("CCTV analytics", { exact: true }).click();
    await review.getByLabel("Review status").selectOption("confirmed");
    await review.getByRole("button", { name: /^Person entered zone · Yard entry/ }).first().click();
    await expect(observation).toContainText("Verified against the yard recording.");
    await expect(observation).toContainText("Pilot Supervisor");
    await posture.getByText("Observe rule report", { exact: true }).click();
    const report = posture.getByRole("region", { name: "Observe rule report", exact: true });
    const yardReport = report.getByRole("region", { name: "Yard CCTV · Yard entry" });
    await expect(yardReport.getByRole("region", { name: "Day", exact: true }).getByText("Confirmed", { exact: true }).locator("../dd")).toHaveText("1");
    await expect(yardReport.getByRole("region", { name: "Night", exact: true }).locator("dd")).toHaveText(["0", "0", "0", "0"]);
    await report.getByLabel("Report source").selectOption("replay");
    await expect(yardReport.getByRole("region", { name: "Day", exact: true }).locator("dd")).toHaveText(["0", "0", "0", "0"]);
    await report.getByLabel("Report version").selectOption("1");
    await expect(report.getByText("No published CCTV rules for the cameras you can access.")).toBeVisible();
    await report.getByLabel("Report version").selectOption("0");
    await report.getByLabel("Report source").selectOption("live");
    await button(report, "Refresh report").click();
    await expect(yardReport.getByRole("region", { name: "Day", exact: true }).getByText(/Mean gateway delivery/)).toBeVisible();
    const hidden = await fetch(gateway + "/api/site/analytics-review?status=all", { headers: { Authorization: "Bearer " + viewerToken } });
    expect((await hidden.json()).entries).toEqual([]);
    const scopedReport = await fetch(gateway + "/api/site/analytics-report", { headers: { Authorization: "Bearer " + viewerToken } }).then((response) => response.json());
    expect(scopedReport.rules.map((rule: { cameraId: string }) => rule.cameraId)).toEqual(["sky-camera"]);
    await button(rail, "Alerts").click();
    const alerts = page.getByRole("region", { name: "Alerts", exact: true });
    await expect(alerts.getByRole("button", { name: /^Person entered zone/ })).toHaveCount(0);
    await alerts.getByRole("button", { name: /^Person remained in zone for .* · Time in yard/ }).first().click({ timeout: 18_000 });
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" }).getByText("Observed time in zone")).toBeVisible();
    await alerts.getByRole("button", { name: /^Vehicle stopped in zone · No stopping/ }).first().click({ timeout: 18_000 });
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText(yardModel.kind + ":");
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText("Cloud");
    await button(inspector, "Replay timeline").click();
    await expect(inspector.getByRole("region", { name: "Selected replay event" })).toContainText("No stopping");
    await button(inspector, "Close replay").click();
    await alerts.getByRole("searchbox", { name: "Search incidents" }).fill("Sky watch");
    await alerts.getByRole("button", { name: /^Possible drone sighted · Sky watch/ }).first().click({ timeout: 28_000 });
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText("owlv2:");
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText("On-premises");
    await expect(inspector.getByText("Sky observation sector", { exact: true })).toBeVisible();
    const foreign = await fetch(gateway + "/api/cameras/sky-camera/analytics", { headers: { Authorization: "Bearer " + groundToken } });
    expect(foreign.status).toBe(401);
    expect((await workerRequest("yard-camera", groundToken, "/api/site/telemetry", {
      type: "health", sensorId: "yard-camera", state: "online", id: "camera-health", occurredAt: new Date().toISOString(),
    })).status).toBe(401);
    await expect.poll(() => cloudClients.size).toBeGreaterThan(0);

    await button(rail, "Posture").click();
    if (await versionSummary.locator("..").getAttribute("open") === null) await versionSummary.click();
    await button(posture, "Edit published configuration as a draft").click();
    await editCamera("Yard CCTV");
    await inspector.locator(".gv-cctv-editor > summary").click();
    await inspector.getByLabel("Analytics compute").selectOption("on-premises");
    await button(inspector, "Save device").click();
    await button(page, "Draft mode · Review changes").click();
    await button(posture, "Save site draft").click();
    await expect(posture.getByText("Shared site draft saved.")).toBeVisible();
    await posture.getByLabel("Version note").fill("Move yard analytics on-premises");
    await button(posture, "Publish saved site draft").click();
    await expect(page.getByText("Version 3", { exact: true })).toBeVisible();
    expect((await workerRequest("yard-camera", groundToken)).status).toBe(403);
    expect(await (await workerRequest("yard-camera", localGroundToken)).json()).toMatchObject({ placement: "on-premises", version: 3 });
    await expect.poll(() => cloudClients.size, { timeout: 9000 }).toBe(0);
    connectivity.closeAllConnections();
    await new Promise<void>((resolve) => connectivity.close(() => resolve()));
    await startWorker("yard", localGroundToken, "ground", yardModel, 640, 960, "on-premises");
    await expect.poll(async () => {
      const status = await fetch(gateway + "/api/site/monitoring", { headers: adminHeaders }).then((response) => response.json());
      return status.siteEvents.some((event: { observation: { sensorId: string; analytics?: { placement: string; policyVersion: number } } }) => {
        const evidence = event.observation.analytics;
        return event.observation.sensorId === "yard-camera" && evidence?.placement === "on-premises" && evidence.policyVersion === 3;
      });
    }, { timeout: 20_000 }).toBe(true);
  } finally {
    for (const client of cloudClients) client.destroy();
    cloudVideo.close();
    connectivity.closeAllConnections(); connectivity.close();
    recorder.closeAllConnections(); recorder.close();
    await Promise.all(children.map(stop));
    await rm(directory, { recursive: true, force: true });
  }
});

test("dock occupancy and factory safety rules are published and reviewed in Chrome", async ({ page, baseURL }) => {
  test.setTimeout(75_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-industry-ui-"));
  const adminToken = "1".repeat(64), workerToken = "2".repeat(64);
  const cameraKey = "c".repeat(48), state = join(directory, "state");
  const access = join(directory, "access.json"), workers = join(directory, "workers.json");
  const archive = join(directory, "axis-fixture.mkv");
  execFileSync("ffmpeg", ["-v", "error", "-i", recorderClip, "-c", "copy", archive]);
  const media = await readFile(archive);
  const recorder = createServer((incoming, outgoing) => {
    const url = new URL(incoming.url ?? "/", "http://127.0.0.1");
    if (url.pathname.endsWith("/list.cgi")) {
      const start = Date.parse(url.searchParams.get("starttime") ?? "");
      outgoing.end(`<root><recording recordingid="dock-fixture" diskid="SD" starttime="${new Date(start).toISOString()}" stoptime="${new Date(start + 4000).toISOString()}"/></root>`);
    } else if (url.pathname.endsWith("/exportrecording.cgi")) outgoing.end(media);
    else outgoing.writeHead(404).end();
  });
  recorder.listen(0, "127.0.0.1"); await once(recorder, "listening");
  const recorderAddress = "http://127.0.0.1:" + (recorder.address() as { port: number }).port;
  await mkdir(state);
  await writeFile(join(state, "cameras.json"), JSON.stringify({ "dock-camera": { key: cameraKey, url: "rtsp://127.0.0.1/dock-fixture" } }));
  await writeFile(access, JSON.stringify({ site: "Industry pilot", siteId: "industry",
    organization: { id: "industry-pilot", name: "Industry Pilot" },
    operators: { admin: { name: "Pilot Reviewer", role: "org-admin", cameras: "*",
      tokenSha256: createHash("sha256").update(adminToken).digest("hex") } } }));
  await writeFile(workers, JSON.stringify({ "dock-camera": { cloud: workerToken } }));
  const child = spawn("python3", [gatewayScript, "--port", "0", "--state-dir", state,
    "--allow-source", "127.0.0.1/32", "--site-access", access, "--analytics-workers", workers, "--axis-recorder", recorderAddress],
    { stdio: ["ignore", "pipe", "pipe"] });
  try {
    const [, port] = await spawnReady(child, /Camera gateway listening on 127\.0\.0\.1:(\d+)/);
    const gateway = "http://127.0.0.1:" + port;
    const api = async (path: string, token: string, body?: object) => {
      const response = await fetch(gateway + "/api/" + path, {
        headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        ...(body && { method: "POST", body: JSON.stringify(body) }),
      });
      expect(response.status, await response.clone().text()).toBe(200);
      return response.json();
    };
    const draft = await api("site/configuration", adminToken, { operation: "save", publishedVersion: 0, draftRevision: 0,
      configuration: { sensors: [{ id: "dock-camera", name: "Dock CCTV", modality: "vision", position: [-122.3, 37.78],
        floorId: "factory-floor", cameraGatewayKind: "rtsp", cameraFeedUrl: `${gateway}/streams/${cameraKey}/index.m3u8` }],
        floors: [{ id: "factory-floor", name: "Factory floor", building: "Warehouse", imageUrl: gateway + "/fixture-floor.png",
          corners: [[-122.31, 37.79], [-122.29, 37.79], [-122.29, 37.77], [-122.31, 37.77]] }],
        boundaries: [{ id: "bay-a", name: "Bay A", kind: "Restricted", enabled: true, sensorIds: ["dock-camera"], floorId: "factory-floor",
          vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] }] } });
    await api("site/configuration", adminToken, { operation: "publish", publishedVersion: 0, draftRevision: draft.draftRevision, message: "Seed dock pilot" });
    await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
    await page.goto(baseURL!);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    const posture = page.getByRole("region", { name: "Posture", exact: true });
    const inspector = page.getByRole("complementary", { name: "Monitor and review" });
    await button(rail, "Posture").click();
    await posture.locator("summary", { hasText: "Shared site operator access" }).click();
    await posture.getByLabel("Shared site gateway URL").fill(gateway);
    await posture.getByLabel("Site operator token").fill(adminToken);
    await button(posture, "Connect operator").click();
    await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
    const configSummary = posture.getByText("Site configuration and versions", { exact: true });
    if (await configSummary.locator("..").getAttribute("open") === null) await configSummary.click();
    await button(posture, "Edit published configuration as a draft").click();
    await button(rail, "Devices").click();
    await page.getByRole("region", { name: "Devices", exact: true }).getByLabel("Site level").selectOption("factory-floor");
    await page.getByRole("region", { name: "Devices", exact: true }).getByRole("button", { name: /^Dock CCTV/ }).click();
    await button(inspector, "Edit device").click();
    await inspector.locator(".gv-cctv-editor > summary").click();
    for (const name of ["dock monitoring", "person in vehicle lane", "stopped vehicle in marked walkway"]) {
      await button(inspector, "Add " + name).click();
      const details = inspector.locator(".gv-cctv-editor > div > details").last();
      await details.locator(":scope > summary").click();
      await expect(details.getByLabel("Rule mode")).toBeDisabled();
      await button(details, "Start with a rectangle").click();
    }
    await button(inspector, "Save device").click();
    await button(page, "Draft mode · Review changes").click();
    await button(posture, "Save site draft").click();
    await expect(posture.getByText("Shared site draft saved.")).toBeVisible();
    await posture.getByLabel("Version note").fill("Dock and factory review");
    await button(posture, "Publish saved site draft").click();
    await expect(page.getByText("Version 2", { exact: true })).toBeVisible();
    const policy = await api("cameras/dock-camera/analytics", workerToken);
    const [dock, lane, walkway] = policy.rules;
    expect(dock).toMatchObject({ trackOccupancy: true, seconds: 2 });
    expect(lane).toMatchObject({ safetyRule: "person-vehicle-lane", seconds: 1 });
    expect(walkway).toMatchObject({ safetyRule: "vehicle-marked-walkway", seconds: 6 });

    const occupancy = posture.getByRole("region", { name: "Dock occupancy" });
    await expect(occupancy).toContainText("Bay A · Unknown");
    const status = (state: string, version: number, ...dockOccupancy: object[]) =>
      api("cameras/dock-camera/analytics", workerToken, { state, version, dockOccupancy, models: ["fixture-yolo11"],
        pending: 0, fps: 4, frameAgeSeconds: .1 });
    const at = () => Date.now() / 1000;
    await status("running", 2, { ruleId: dock.id, state: "vacant", sampledAt: at(), changedAt: at() - 1 });
    await expect(occupancy).toContainText("Bay A · Vacant", { timeout: 12_000 });
    const arrival = "cctv-" + "e".repeat(32);
    async function observation(rule: typeof dock, id: string, occurredAt = new Date().toISOString()) {
      await api("site/telemetry", workerToken, { type: "observation", id, sensorId: "dock-camera",
        eventType: rule.kind, occurredAt, summary: rule.name,
        analytics: { policyVersion: 2, ruleId: rule.id, object: rule.object, score: .90, box: [.3, .3, .5, .6],
          frame: [640, 480], source: "live", trackId: "fixture-track", model: "fixture-yolo11" } });
    }
    const arrivalAt = new Date(Math.max(Date.now() - 12_000, policy.publishedAt * 1000 + 250)).toISOString();
    await observation(dock, arrival, arrivalAt);
    await status("running", 2, { ruleId: dock.id, state: "occupied", sampledAt: at(), changedAt: at() - 1, arrivalEventId: arrival });
    await expect(occupancy).toContainText("Bay A · Occupied", { timeout: 12_000 });
    await expect(occupancy).toContainText("Observed occupied for");
    const lookup = page.waitForRequest((request) => request.url().includes(`/streams/${cameraKey}/recording.json`));
    await button(occupancy, "Review arrival").click();
    expect(new URL((await lookup).url()).searchParams.get("at")).toBe(String(Date.parse(arrivalAt)));
    await status("running", 2, { ruleId: dock.id, state: "occupied", sampledAt: at(), changedAt: Date.parse(arrivalAt) / 1000, arrivalEventId: arrival });
    await expect(occupancy.locator("video")).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => occupancy.locator("video").evaluate((element: HTMLVideoElement) => element.videoWidth),
      { timeout: 8000 }).toBeGreaterThan(0);
    await button(occupancy, "Close arrival recording").click();
    await occupancy.getByText("Shared dock schedule", { exact: true }).click();
    const comparison = occupancy.getByRole("region", { name: "Shared dock schedule" });
    const from = new Date(Date.parse(arrivalAt) - 60_000).toISOString(), until = new Date(Date.parse(arrivalAt) + 60_000).toISOString();
    async function upload(...rows: string[]) {
      await comparison.getByLabel("Dock appointments CSV").setInputFiles({ name: "fixture-appointments.csv", mimeType: "text/csv",
        buffer: Buffer.from("appointmentId,boundaryId,startsAt,endsAt\n" + rows.join("\n")) });
    }
    await upload(`delivery-1,bay-a,${from},${until}`, `delivery-2,bay-a,${until},${new Date(Date.parse(until) + 60_000).toISOString()}`);
    await button(comparison, "Publish schedule").click();
    await expect(comparison).toContainText("delivery-1 · Arrived");
    await expect(comparison).toContainText("delivery-2 · No arrival observed");
    await upload(`delivery-1,bay-a,${from},${until}`, `delivery-3,bay-a,${from},${until}`);
    await button(comparison, "Publish schedule").click();
    await expect(comparison.getByText(/Ambiguous match/)).toHaveCount(2);
    await upload(`outside,foreign-bay,${from},${until}`);
    await expect(comparison.getByRole("alert")).toContainText("appointments for assigned bays");
    await button(occupancy, "Open camera").click();
    await expect(inspector.getByRole("heading", { name: "Dock CCTV", exact: true })).toBeVisible();
    await button(rail, "Boundaries").click();
    await page.getByRole("region", { name: "Boundaries", exact: true }).getByLabel("Site level").selectOption("factory-floor");
    await page.getByRole("button", { name: /^Bay A/ }).click();
    await expect(inspector.getByRole("region", { name: "Dock occupancy" })).toContainText("Bay A · Occupied");
    await status("no-video", 2);
    await expect(inspector.getByRole("region", { name: "Dock occupancy" })).toContainText("Bay A · Unknown", { timeout: 12_000 });
    await status("running", 2, { ruleId: dock.id, state: "occupied", sampledAt: at() - 20, changedAt: at() - 25 });
    await button(rail, "Posture").click();
    await expect(occupancy).toContainText("Bay A · Unknown");
    await observation(lane, "fixture-lane");
    await observation(walkway, "fixture-stop");
    await posture.getByText("CCTV analytics", { exact: true }).click();
    const review = posture.getByRole("region", { name: "Observed CCTV detections" });
    await review.getByLabel("Review camera").selectOption("dock-camera");
    await review.getByLabel("Current published version").check();
    await review.getByLabel("Factory safety").selectOption("all");
    await expect(review.getByRole("button", { name: /Person in vehicle lane/ })).toBeVisible();
    await expect(review.getByRole("button", { name: /Stopped vehicle in marked walkway/ })).toBeVisible();
    await expect(review.getByRole("button", { name: /Dock monitoring/ })).toHaveCount(0);
    await review.getByLabel("Factory safety").selectOption("person-vehicle-lane");
    await review.getByRole("button", { name: /Person in vehicle lane/ }).click();
    const observationPanel = review.getByRole("region", { name: "Selected CCTV observation" });
    await expect(observationPanel).toContainText("Version 2 · fixture-yolo11");
    await observationPanel.getByLabel("Review decision").selectOption("confirmed");
    await observationPanel.getByLabel("Review note").fill("Reviewed the fixture for the lane pilot.");
    await button(observationPanel, "Save detection review").click();
    await expect(review).toContainText("Detection review saved.");
    await review.getByLabel("Review status").selectOption("confirmed");
    await review.getByRole("button", { name: /Person in vehicle lane/ }).click();
    await expect(observationPanel).toContainText("Reviewed the fixture for the lane pilot.");
    await button(rail, "Alerts").click();
    await expect(page.getByRole("region", { name: "Alerts", exact: true })).toContainText("0 matching incidents");
  } finally {
    recorder.closeAllConnections(); recorder.close();
    if (child.exitCode === null && child.signalCode === null) {
      child.kill();
      await once(child, "exit");
    }
    await rm(directory, { recursive: true, force: true });
  }
});

test("simulated inspection results stay scoped and open station evidence in Chrome", async ({ page, baseURL }) => {
  test.setTimeout(65_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-inspection-station-"));
  const admin = "1".repeat(64), adapter = "2".repeat(64), viewer = "3".repeat(64);
  const roles = join(directory, "roles.json"), devices = join(directory, "devices.json"), token = join(directory, "adapter.token");
  const simulator = fileURLToPath(new URL("../../../scripts/dev/replay_inspection_results.py", import.meta.url));
  const hash = (value: string) => createHash("sha256").update(value).digest("hex");
  await writeFile(roles, JSON.stringify({ site: "Assembly pilot", siteId: "assembly-pilot", organization: { id: "plant", name: "Pilot Plant" },
    operators: { admin: { name: "Plant supervisor", role: "org-admin", cameras: "*", tokenSha256: hash(admin) },
      viewer: { name: "Other camera operator", role: "viewer", cameras: ["other-camera"], tokenSha256: hash(viewer) } } }));
  await writeFile(devices, JSON.stringify({ "station-camera": adapter }));
  await writeFile(token, adapter, { mode: 0o600 });
  const child = spawn("python3", [gatewayScript, "--port", "0", "--state-dir", join(directory, "state"),
    "--allow-source", "127.0.0.1/32", "--site-access", roles, "--telemetry-devices", devices], { stdio: ["ignore", "pipe", "pipe"] });
  try {
    const [, port] = await spawnReady(child, /Camera gateway listening on 127\.0\.0\.1:(\d+)/);
    const gateway = "http://127.0.0.1:" + port;
    const api = (path: string, credential: string, body?: object) => fetch(gateway + "/api/site/" + path, {
      headers: { Authorization: "Bearer " + credential, "Content-Type": "application/json" },
      ...(body && { method: "POST", body: JSON.stringify(body) }),
    });
    const draft = await api("configuration", admin, { operation: "save", publishedVersion: 0, draftRevision: 0,
      configuration: { sensors: [
        { id: "station-camera", name: "Station CCTV", modality: "vision", position: [-122.3, 37.78] },
        { id: "other-camera", name: "Other CCTV", modality: "vision", position: [-122.3, 37.78] },
      ], boundaries: [{ id: "assembly", name: "Assembly cell", kind: "Restricted", enabled: true, sensorIds: ["station-camera"],
        vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] }] } });
    expect(draft.status, await draft.clone().text()).toBe(200);
    const published = await api("configuration", admin, { operation: "publish", publishedVersion: 0,
      draftRevision: (await draft.json()).draftRevision, message: "Seed station camera" });
    expect(published.status).toBe(200);

    await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
    await page.goto(baseURL!);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    const posture = page.getByRole("region", { name: "Posture", exact: true });
    const inspector = page.getByRole("complementary", { name: "Monitor and review" });
    await button(rail, "Posture").click();
    await posture.locator("summary", { hasText: "Shared site operator access" }).click();
    await posture.getByLabel("Shared site gateway URL").fill(gateway);
    await posture.getByLabel("Site operator token").fill(admin);
    await button(posture, "Connect operator").click();
    await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
    const configuration = posture.getByText("Site configuration and versions", { exact: true });
    if (await configuration.locator("..").getAttribute("open") === null) await configuration.click();
    await button(posture, "Edit published configuration as a draft").click();
    await button(rail, "Devices").click();
    await page.getByRole("region", { name: "Devices", exact: true }).getByRole("button", { name: /^Station CCTV/ }).click();
    await button(inspector, "Edit device").click();
    const input = inspector.getByRole("textbox", { name: "Inspection station ID (pilot)" });
    await input.fill("invalid station");
    await expect(button(inspector, "Save device")).toBeDisabled();
    await input.fill("assembly-line-2");
    await button(inspector, "Save device").click();
    await button(page, "Draft mode · Review changes").click();
    await button(posture, "Save site draft").click();
    await expect(posture.getByText("Shared site draft saved.")).toBeVisible();
    await posture.getByLabel("Version note").fill("Map a simulated component station");
    await button(posture, "Publish saved site draft").click();
    await expect(page.getByText("Version 2", { exact: true })).toBeVisible();

    const started = new Date().toISOString();
    const replay = () => runProcess("python3", [simulator, "--gateway", gateway, "--camera", "station-camera", "--token-file", token,
      "--run-id", "chrome-fixture", "--start-at", started]);
    expect(JSON.parse((await replay()).stdout)).toEqual({ accepted: 2, duplicates: 0, skippedIncomplete: 3 });
    expect(JSON.parse((await replay()).stdout)).toEqual({ accepted: 0, duplicates: 2, skippedIncomplete: 3 });
    expect((await api("inspection-results", adapter)).status).toBe(401);
    const hidden = await api("inspection-results?camera=station-camera", viewer);
    expect(hidden.status).toBe(200);
    expect((await hidden.json()).entries).toEqual([]);

    await posture.getByText("Inspection results", { exact: true }).click();
    const results = posture.getByRole("region", { name: "Inspection results", exact: true });
    await expect(results.getByRole("button", { name: /^Component presence/ })).toHaveCount(2);
    await results.getByLabel("Inspection camera").selectOption("station-camera");
    await results.getByLabel("Inspection outcome").selectOption("fail");
    await expect(results.getByRole("button", { name: /^Component presence/ })).toHaveCount(1);
    await results.getByRole("button", { name: /^Component presence failed/ }).click();
    const selected = results.getByRole("region", { name: "Selected inspection result" });
    await expect(selected).toContainText("Station CCTV · assembly-line-2 · Version 2");
    await expect(selected).toContainText("Recipe bracket-v3 · Component retaining-clip");
    await expect(selected).toContainText("Part frame-002 · Lot lot-a");
    await expect(selected).toContainText("Assembly cell");
    await button(selected, "Find station recording").click();
    await expect(selected.getByRole("status")).toHaveText("Recorder lookup is unavailable for this camera.");
    await button(selected, "Close station recording").click();
    await button(selected, "Open station camera").click();
    await expect(inspector.getByRole("heading", { name: "Station CCTV", exact: true })).toBeVisible();
    await button(rail, "Alerts").click();
    await expect(page.getByRole("region", { name: "Alerts", exact: true })).toContainText("0 matching incidents");
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill();
      await once(child, "exit");
    }
    await rm(directory, { recursive: true, force: true });
  }
});

test("a twelve-camera site sustains YOLO11 inference and reports each camera in Posture", async ({ page, baseURL }, testInfo) => {
  await exerciseCameraSite(page, baseURL!, testInfo, 12);
});

test("a twelve-camera twilight stair site reports all reviewed crossings", async ({ page, baseURL }, testInfo) => {
  await exerciseCameraSite(page, baseURL!, testInfo, 12, true);
});

test("a twelve-camera mixed site reports live dock arrivals and entrance crossings", async ({ page, baseURL }, testInfo) => {
  await exerciseCameraSite(page, baseURL!, testInfo, 12, false, true);
});

test("a twelve-camera native mix reports ONVIF events with eleven YOLO11 feeds", async ({ page, baseURL }, testInfo) => {
  await exerciseCameraSite(page, baseURL!, testInfo, 12, false, false, true);
});

test("YOLO11 rule detail follows the published camera rule in Posture", async ({ page, baseURL }, testInfo) => {
  await exerciseCameraSite(page, baseURL!, testInfo, 1);
});

async function exerciseCameraSite(page: Page, baseURL: string, testInfo: TestInfo, cameraCount: number, stairs = false, dock = false, native = false) {
  const video = stairs ? process.env.PROXIGO_CCTV_STAIRS_VIDEO : siteVideo;
  const dockVideo = dock ? process.env.PROXIGO_CCTV_DOCK_VIDEO : undefined;
  test.skip(![python, yolo11, video, mediaMtx].every(Boolean) || dock && !dockVideo,
    "Configure the YOLO11s ONNX export, MEVA source footage, and MediaMTX.");
  test.setTimeout(240_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-cctv-site-"));
  const children: ChildProcess[] = [];
  let nativeFixture: Awaited<ReturnType<typeof onvifEventsFixture>> | undefined;
  let inferenceFixture: Awaited<ReturnType<typeof localInferenceFixture>> | undefined;
  const intelligenceLoad = !stairs && !dock && !native && cameraCount === 12 && process.env.PROXIGO_SITE_INTELLIGENCE_LOAD === "1";
  const start = (bin: string, args: string[]) => {
    const child = spawn(bin, args, { stdio: ["ignore", "pipe", "pipe"] });
    children.push(child);
    return child;
  };
  try {
    const dockFixture = dock ? JSON.parse(await readFile(dockReference, "utf8")) : undefined;
    if (stairs || dockFixture) {
      const checksum = createHash("sha256");
      for await (const chunk of createReadStream(dockVideo ?? video!)) checksum.update(chunk);
      const reference = dockFixture ?? JSON.parse(await readFile(stairReference, "utf8"));
      expect(checksum.digest("hex"), "Use the original reviewed MEVA recording").toBe(reference.sourceSha256);
    }
    const stopVideo = !stairs && !dock && !native && cameraCount === 12 ? process.env.PROXIGO_CCTV_STOP_VIDEO : undefined;
    const dwellVideo = !stairs && !dock && !native && cameraCount === 12 ? process.env.PROXIGO_CCTV_DWELL_VIDEO : undefined;
    const specialVideo = dockVideo ?? stopVideo ?? dwellVideo;
    const rotateCanary = !stairs && process.env.PROXIGO_CCTV_ROTATED_CANARY === "1";
    const ruleDetail = stairs ? undefined : process.env.PROXIGO_CCTV_RULE_DETAIL_CANARY;
    const splitDetailCanary = ruleDetail === "2";
    const ruleDetailCanary = cameraCount === 1 || ruleDetail === "1" || splitDetailCanary;
    const detailPose = stairs ? undefined : process.env.PROXIGO_CCTV_YOLO11_DETAIL_POSE;
    if (specialVideo) expect(rotateCanary || ruleDetailCanary).toBe(false);
    if (stopVideo) expect(!!detailPose || !!dwellVideo).toBe(false);
    if (dwellVideo) expect(!!detailPose).toBe(true);
    const specialCount = dock ? 1 : specialVideo
      ? Number((stopVideo ? process.env.PROXIGO_CCTV_STOP_CAMERAS : process.env.PROXIGO_CCTV_DWELL_CAMERAS) ?? "1") : 0;
    if (specialVideo) expect(Number.isInteger(specialCount) && specialCount >= 1 && specialCount <= cameraCount).toBe(true);
    const dwellHeight = Number(process.env.PROXIGO_CCTV_DWELL_HEIGHT ?? "1080");
    if (dwellVideo) expect([720, 1080]).toContain(dwellHeight);
    const dwellSourceFps = Number(process.env.PROXIGO_CCTV_DWELL_SOURCE_FPS ?? "4");
    if (dwellVideo) expect([4, 15, 30]).toContain(dwellSourceFps);
    const dwellSourcePreset = process.env.PROXIGO_CCTV_DWELL_SOURCE_PRESET ?? "ultrafast";
    if (dwellVideo) expect(["ultrafast", "veryfast"]).toContain(dwellSourcePreset);
    const dwellCycles = dwellVideo ? Number(process.env.PROXIGO_CCTV_DWELL_CYCLES ?? "0") : 0;
    expect(Number.isInteger(dwellCycles) && dwellCycles >= 0 && dwellCycles <= 4).toBe(true);
    if (dwellCycles) test.setTimeout(360_000);
    const cameras = Array.from({ length: cameraCount }, (_, index) => {
      const number = String(index + 1).padStart(2, "0");
      return { id: "camera-" + number, name: "Camera " + number, rotated: rotateCanary && index === cameraCount - 1,
        token: createHash("sha256").update("load-test-camera-" + number).digest("hex") };
    });
    const canaryId = cameras[cameras.length - 1].id;
    const nativeId = native ? canaryId : undefined;
    const workerCameras = cameras.filter(({ id }) => id !== nativeId);
    const specialCameraIds = new Set(cameras.slice(cameraCount - specialCount).map(({ id }) => id));
    const reserve = reservePort();
    reserve.listen(0, "127.0.0.1");
    await once(reserve, "listening");
    const port = (reserve.address() as { port: number }).port;
    await new Promise<void>((resolve) => reserve.close(() => resolve()));
    const rtsp = `rtsp://127.0.0.1:${port}`;
    const mt = join(directory, "mediamtx.yml");
    await writeFile(mt, `rtspAddress: 127.0.0.1:${port}\nrtspTransports: [tcp]\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  all_others:\n`);
    await spawnReady(start(mediaMtx!, [mt]), /\[RTSP\].*(?:started|listener opened)/);
    const encoded = join(directory, "entrance.mp4");
    await runProcess("ffmpeg", ["-v", "error", "-i", video!, ...(stairs ? ["-ss", "26", "-t", "55"] : []),
      "-an", "-vf", stairs ? "fps=4,scale=536:960" : "fps=4,scale=640:358",
      "-c:v", "libx264", "-preset", stairs ? "veryfast" : "ultrafast", "-tune", "zerolatency",
      ...(stairs ? ["-qp", "0"] : []), "-g", "4", "-pix_fmt", "yuv420p", encoded]);
    const sideways = join(directory, "entrance-sideways.mp4");
    if (rotateCanary) {
      await runProcess("ffmpeg", ["-v", "error", "-i", encoded, "-an", "-vf", "transpose=cclock",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "4", "-pix_fmt", "yuv420p", sideways]);
    }
    const approach = join(directory, "approach.mp4");
    if (specialVideo) {
      const sourceFps = dockFixture ? dockFixture.fps : stopVideo ? 8 : dwellSourceFps;
      const filter = `fps=${sourceFps},scale=1920:1080`;
      const input = dockFixture ? ["-ss", String(dockFixture.startSeconds), "-i", specialVideo, "-t", String(dockFixture.durationSeconds)]
        : ["-i", specialVideo];
      await runProcess("ffmpeg", ["-v", "error", ...input, "-an", "-vf", filter,
        "-c:v", "libx264", "-preset", stopVideo ? "ultrafast" : dwellSourcePreset, "-tune", "zerolatency", "-crf", "20", "-maxrate", "5500k",
        "-bufsize", "5500k", "-g", String(Math.max(8, sourceFps)), "-pix_fmt", "yuv420p", approach]);
    }
    const clocks = new Map<string, () => number>();
    await Promise.all(workerCameras.map(({ id, rotated }) => {
      const monitor = stairs || (dwellCycles > 0 && specialCameraIds.has(id));
      const publisher = start("ffmpeg", [...(monitor ? ["-progress", "pipe:1", "-stats_period", "0.25"] : []),
        "-re", "-stream_loop", "-1", "-i", specialCameraIds.has(id) ? approach : rotated ? sideways : encoded,
        "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", rtsp + "/" + id]);
      if (monitor) clocks.set(id, publisherClock(publisher));
      return spawnReady(publisher, /Output #0, rtsp/);
    }));

    const admin = "a".repeat(64);
    const headers = { "Content-Type": "application/json", Authorization: "Bearer " + admin };
    const access = join(directory, "access.json"), workers = join(directory, "workers.json");
    await writeFile(access, JSON.stringify({ site: "CCTV pilot", siteId: "load-site",
      organization: { id: "load-org", name: "CCTV pilot" }, operators: { admin: {
        name: "Site supervisor", role: "org-admin", cameras: "*", tokenSha256: createHash("sha256").update(admin).digest("hex"),
      } } }));
    await writeFile(workers, JSON.stringify(Object.fromEntries(workerCameras.map(({ id, token }) => [id, { "on-premises": token }]))));
    const nativeArgs: string[] = [];
    const gateDoor = { id: "load-gate", name: "Gate door", position: [-122.3, 37.78], modality: "access", linkedCameraId: cameras[0].id };
    const gateToken = "f".repeat(64), cameraKey = "e".repeat(48);
    if (intelligenceLoad) {
      inferenceFixture = await localInferenceFixture(14_000);
      const settings = join(directory, "site-intelligence.json"), adapters = join(directory, "load-adapters.json");
      await writeFile(settings, JSON.stringify({ ai: { provider: "on-premises", localOrigin: inferenceFixture.origin, handoff: true },
        deviceDegradation: true }));
      await writeFile(adapters, JSON.stringify({ [gateDoor.id]: gateToken }));
      await mkdir(join(directory, "gateway"));
      await writeFile(join(directory, "gateway", "cameras.json"), JSON.stringify({ [cameras[0].id]: { key: cameraKey, url: rtsp + "/" + cameras[0].id } }));
      nativeArgs.push("--site-intelligence", settings, "--telemetry-devices", adapters);
    }
    if (native) {
      nativeFixture = await onvifEventsFixture();
      const enrollment = join(directory, "camera-native.json");
      await writeFile(enrollment, JSON.stringify([{ cameraId: nativeId, address: nativeFixture.address, ruleId: "entrance",
        deviceRule: "fixture-line", videoSourceToken: "fixture-video" }]));
      nativeArgs.push("--onvif-analytics", enrollment);
    }
    const gatewayPort = (await spawnReady(start("python3", [gatewayScript, "--port", "0", "--state-dir", join(directory, "gateway"),
      "--allow-source", "127.0.0.1/32", "--site-access", access, "--analytics-workers", workers, ...nativeArgs]), /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
    const gateway = "http://127.0.0.1:" + gatewayPort;
    const update = (payload: object) => fetch(gateway + "/api/site/configuration", { headers, method: "POST", body: JSON.stringify(payload) });
    const specialRule = dockFixture ? dockFixture.policy.rules[0] : stopVideo ? { id: "roadway-stop", name: "Roadway stop", kind: "restricted-stop", object: "vehicle",
      points: [[.326, .214], [.40, .214], [.40, .269], [.326, .269]], seconds: .5, requireMovement: true,
    } : { id: "doorway-dwell", name: "Doorway dwell", kind: "zone-dwell", object: "person",
      points: [[.64, .39], [.72, .39], [.72, .56], [.64, .56]], seconds: 15, dwellGapSeconds: .75,
    };
    const secondDetailRule = { id: "upper-walkway", name: "Upper walkway", kind: "zone-entry", object: "person",
      boundaryId: "entrance", mode: "observe", minimumScore: .25, minimumPixels: 12, seconds: .5,
      points: [[.04, .08], [.14, .08], [.14, .18], [.04, .18]] };
    const stairsRule = { id: "stairs", name: "School stairs crossing", kind: "line-crossing", object: "person",
      boundaryId: "entrance", mode: "alert", minimumScore: .25, minimumPixels: 12, seconds: .5,
      direction: "negative", scaleCrossingBuffer: true, points: [[.58, .70], [.58, .91]] };
    const configuration = {
      sensors: [...cameras.map(({ id, name, rotated }) => ({ id, name, modality: "vision", position: [-122.3, 37.78], analyticsPlacement: "on-premises",
        analyticsRules: stairs ? [stairsRule] : specialCameraIds.has(id) ? [{ minimumScore: .25, minimumPixels: 12, ...specialRule, boundaryId: "entrance", mode: "observe",
        }] : [{ id: "entrance", name: "Entrance crossing", kind: "line-crossing", object: "person", boundaryId: "entrance",
          mode: "observe", points: rotated ? [[.5, .48], [1, .48]] : [[.52, .5], [.52, 1]],
          direction: "negative", minimumScore: .25, minimumPixels: 12, seconds: .5, ...(id === nativeId && { cameraNative: true }) },
          ...(splitDetailCanary && id === canaryId ? [secondDetailRule] : [])] })), ...(intelligenceLoad ? [gateDoor] : [])],
      boundaries: [{ id: "entrance", name: "Entrance", kind: "Restricted", enabled: true,
        sensorIds: [...cameras.map(({ id }) => id), ...(intelligenceLoad ? [gateDoor.id] : [])],
        vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] }],
    };
    const seed = await update({ operation: "save", publishedVersion: 0, draftRevision: 0, configuration });
    expect(seed.status, await seed.clone().text()).toBe(200);
    const published = await update({ operation: "publish", publishedVersion: 0, draftRevision: (await seed.json()).draftRevision,
      message: "Publish camera sites and rules" });
    expect(published.status).toBe(200);
    const publishedRevision = (await published.json()).draftRevision;

    const canary = stairs ? undefined : process.env.PROXIGO_CCTV_YOLO11_CANARY ?? (detailPose ? yolo11 : undefined);
    const sha256 = createHash("sha256").update(await readFile(yolo11!)).digest("hex");
    const defaultModel = { path: yolo11, sha256 };
    const canaryModel = {
      ...(canary ? { path: canary, sha256: createHash("sha256").update(await readFile(canary)).digest("hex") } : defaultModel),
      ...(stairs ? { detailFromRules: true }
        : dockFixture ? { detailRegion: dockFixture.detailRegion, detailObjects: ["vehicle"] }
        : stopVideo ? { detailRegion: [.28, .16, .49, .36], detailObjects: ["vehicle"], vehicleStopConfirmationBoost: true }
        : dwellVideo ? { detailRegion: [.52, .27, .83, .67], detailPoseFallback: true }
        : ruleDetailCanary ? { detailFromRules: true, detailRegionLimit: splitDetailCanary ? 2 : 1 } : canary ? {
        detailRegion: rotateCanary ? [.2, .14, 1, .8] : [.2, .2, .86, 1], detailObjects: detailPose ? ["person"] : ["person", "vehicle"],
      } : {}),
      ...(detailPose ? { detailPose: { path: detailPose, sha256: createHash("sha256").update(await readFile(detailPose)).digest("hex") } } : {}),
      ...(rotateCanary ? { rotationDegrees: 90 } : {}),
    };
    const modelByCamera = new Map(cameras.map(({ id }) => [id, stairs || id === canaryId || specialCameraIds.has(id) ? canaryModel : defaultModel] as const));
    const modelMatches = (id: string, actual: string | undefined) => {
      const model = modelByCamera.get(id);
      if (!model) return false;
      const prefix = "yolo11:" + model.sha256.slice(0, 16) + ("rotationDegrees" in model && model.rotationDegrees ? ":r90" : "");
      const detailSuffix = "detailFromRules" in model && model.detailFromRules ? ":rule-detail:" :
        "detailRegion" in model && model.detailRegion ? ":detail:" : "";
      return detailSuffix ? !!actual?.match(new RegExp("^" + prefix + detailSuffix + "[0-9a-f]{8}$")) : actual === prefix;
    };
    const coremlCacheDirectory = yolo11Provider === "CoreMLExecutionProvider" ? join(directory, "coreml-cache") : undefined;
    async function startCamera({ id, rotated, token }: typeof cameras[number]) {
      const tokenFile = join(directory, id + ".token"), config = join(directory, id + ".json");
      const special = specialCameraIds.has(id);
      const height = stairs ? 960 : special ? (stopVideo ? 1080 : dwellHeight) : rotated ? 640 : 358;
      const width = stairs ? 536 : special ? Math.round(height * 16 / 9) : rotated ? 358 : 640;
      await writeFile(tokenFile, token);
      await writeFile(config, JSON.stringify({ gateway, placement: "on-premises", siteId: "load-site", organizationId: "load-org",
        tokenFile, source: rtsp + "/" + id, sensorId: id, state: join(directory, id + ".sqlite3"),
        width, height, fps: special && stopVideo ? 8 : 4, ...(stairs ? { liveFrameBufferMs: 350 } : {}),
        models: { ground: { kind: "yolo11", ...modelByCamera.get(id), provider: yolo11Provider, coremlCacheDirectory } } }));
      start(python!, [workerScript, "--config", config]).stderr?.on("data", (data: Buffer) => process.stderr.write(data));
    }
    const siteMonitoring = () => fetch(gateway + "/api/site/monitoring", { headers }).then((response) => response.json());
    // Measure throughput after all workers reach steady inference.
    for (let offset = 0; offset < workerCameras.length; offset += 3) {
      const batch = workerCameras.slice(offset, offset + 3);
      await Promise.all(batch.map(startCamera));
      await expect.poll(async () => {
        const { analyticsWorkers } = await siteMonitoring();
        return batch.filter(({ id }) => {
          const worker = analyticsWorkers[id];
          return worker?.state !== "running" || !modelMatches(id, worker.models[0]) || worker.fps === 0;
        }).map(({ id }) => id);
      }, { message: "Wait for this group of camera workers to finish startup", timeout: 42_000, intervals: [1000] }).toEqual([]);
    }
    let lastReport = 0;
    const rates = [];
    const dockReports = new Map<number, { state: string; arrivalEventId?: string; sampledAt: number }>();
    const dockEvents = new Set<string>();
    const windows = stairs || dwellCycles ? 0 : cameraCount === 1 ? 1 : 2;
    let intelligenceJob: string | undefined;
    const sitePost = (route: string, body: object, token = admin) => fetch(gateway + "/api/site/" + route, {
      headers: { ...headers, Authorization: "Bearer " + token }, method: "POST", body: JSON.stringify(body),
    });
    const siteRead = async (route: string) => {
      const response = await fetch(gateway + "/api/site/" + route, { headers });
      expect(response.status).toBe(200);
      return response.json();
    };
    for (let window = 0; window < windows; window++) {
      if (window === 1 && inferenceFixture) {
        const stream = gateway + "/streams/" + cameraKey + "/";
        const warm = await fetch(stream + "index.m3u8", { signal: AbortSignal.timeout(20000) });
        expect(warm.status, "Live playback starts before concurrent AI work").toBe(200);
        const trigger = await sitePost("telemetry", { type: "observation", sensorId: gateDoor.id, id: "load-forced",
          eventType: "door-forced-open", occurredAt: new Date().toISOString(), summary: "Load fixture forced door" }, gateToken);
        expect(trigger.status).toBe(200);
        const incidentId = (await siteRead("dispatch")).alerts.find((row: { cameraId?: string }) => row.cameraId === cameras[0].id).id;
        const claim = await sitePost("incident", { incidentId, cameraId: cameras[0].id, revision: 0, action: { kind: "claim" } });
        expect(claim.status).toBe(200);
        const request = await sitePost("ai-jobs", { type: "handoff", incidentId });
        expect(request.status).toBe(202);
        intelligenceJob = (await request.json()).id;
        await expect.poll(() => inferenceFixture?.requests.length, { timeout: 4000 }).toBe(1);
        const playlist = await fetch(stream + "index.m3u8").then((response) => response.text());
        const segment = playlist.match(/^segment-\d+\.ts$/m)?.[0];
        expect(segment).toBeTruthy();
        const playback = await fetch(stream + segment);
        expect(playback.status).toBe(200);
        expect((await playback.arrayBuffer()).byteLength).toBeGreaterThan(0);
      }
      const sent = nativeFixture?.announce(window + 1);
      if (sent) nativeFixture?.announce(window + 1, sent);
      const observationsSince = specialVideo ? Date.now() : 0;
      let snapshot: { id: string; fps: number; ready: boolean; observed: boolean; reportedAt: number; observedAt?: string }[] = [];
      const sample = async () => {
        const status = await siteMonitoring();
        const observed = new Map<string, string>();
        for (const { observation } of status.siteEvents) {
          if (observation?.analytics?.source !== "live" || !modelMatches(observation.sensorId, observation.analytics.model)) continue;
          if (Date.parse(observation.occurredAt) < observationsSince) continue;
          let requiredEvent = observation.eventType === "line-crossing";
          if (specialCameraIds.has(observation.sensorId)) {
            if (dockFixture) {
              requiredEvent = observation.eventType === "zone-entry" && observation.analytics.ruleId === specialRule.id;
              if (requiredEvent) dockEvents.add(observation.id);
            } else if (stopVideo) {
              // Final annotated G336 stop box.
              requiredEvent = observation.eventType === "restricted-stop" && observation.analytics.movementObserved === true &&
                overlapsReferenceBox(observation.analytics.box, [.348958, .218519, .370313, .247222])
            } else {
              requiredEvent = matchesDoorwayDwell(observation);
            }
          }
          if (requiredEvent && !observed.has(observation.sensorId)) observed.set(observation.sensorId, observation.occurredAt);
        }
        snapshot = workerCameras.map(({ id }) => {
          const worker = status.analyticsWorkers[id];
          const dockSample = dockFixture && specialCameraIds.has(id) && worker?.dockOccupancy?.[0];
          if (dockSample) dockReports.set(worker.reportedAt, dockSample);
          return { id, fps: worker?.fps ?? 0, reportedAt: worker?.reportedAt ?? 0, observed: observed.has(id), observedAt: observed.get(id),
            ready: worker?.state === "running" && modelMatches(id, worker.models[0]) && worker.pending === 0 &&
              worker.frameAgeSeconds !== null && worker.frameAgeSeconds < 1 && worker.reportedAt > lastReport &&
              (!intelligenceJob || worker.reportedAt * 1000 >= inferenceFixture!.requests[0].startedAt) };
        });
        return snapshot.filter((camera) => !camera.ready || !camera.observed || camera.fps < 3.5).map(({ id, fps, ready, observed }) =>
          ({ id, fps, ready, observed }));
      };
      try {
        await expect.poll(sample, { message: "Expected a live observation and at least 3.5 inference fps from each camera",
          timeout: 60_000, intervals: [1000] }).toEqual([]);
      } catch (error) {
        await testInfo.attach("Final camera sample before site gate failure", {
          body: JSON.stringify({ stopCameras: stopVideo ? [...specialCameraIds] : [], dwellCameras: dwellVideo ? [...specialCameraIds] : [],
            dwellHeight: dwellVideo ? dwellHeight : null, dwellSourceFps: dwellVideo ? dwellSourceFps : null,
            dwellSourcePreset: dwellVideo ? dwellSourcePreset : null,
            rates, snapshot, observationsSince }, null, 2), contentType: "application/json",
        });
        throw error;
      }
      lastReport = Math.max(...snapshot.map((camera) => camera.reportedAt));
      rates.push(snapshot);
    }
    if (intelligenceJob && inferenceFixture) {
      const request = inferenceFixture.requests[0];
      await expect.poll(async () => (await siteRead("ai-jobs/" + intelligenceJob)).state, { timeout: 20000 }).toBe("ready");
      expect(request.endedAt).toBeGreaterThan(lastReport * 1000);
      expect(request.frames).toHaveLength(0);
      await testInfo.attach("Concurrent site intelligence load", {
        body: JSON.stringify({ cameraCount, playback: "segment delivered", state: "ready",
          startedAt: request.startedAt, lastCameraReport: lastReport * 1000, endedAt: request.endedAt }),
        contentType: "application/json",
      });
    }
    if (nativeFixture) {
      const read = () => fetch(gateway + "/api/site/camera-native", { headers }).then((response) => response.json());
      await expect.poll(async () => (await read()).events.length, { timeout: 12_000 }).toBe(windows);
      const result = await read();
      expect(result.cameras).toEqual([expect.objectContaining({ cameraId: nativeId, state: "supported" })]);
      expect(result.events.every((event: { cameraId: string; observation: { nativeAnalytics: { source: string } } }) =>
        event.cameraId === nativeId && event.observation.nativeAnalytics.source === "onvif-profile-m")).toBe(true);
      expect((await siteMonitoring()).analyticsWorkers[nativeId!]).toBeUndefined();
      await testInfo.attach("Native fixture events and protocol connections", {
        body: JSON.stringify({ ...result, protocol: nativeFixture.stats() }, null, 2), contentType: "application/json",
      });
    }
    if (windows) await testInfo.attach("YOLO11 per-camera throughput", {
      body: JSON.stringify({ provider: yolo11Provider, canary: canary ? canaryModel.sha256 : null, rotateCanary,
        detailPose: canaryModel.detailPose?.sha256 ?? null, ruleDetailCanary, splitDetailCanary, stopCameras: stopVideo ? [...specialCameraIds] : [],
        dwellCameras: dwellVideo ? [...specialCameraIds] : [], dwellHeight: dwellVideo ? dwellHeight : null,
        dwellSourceFps: dwellVideo ? dwellSourceFps : null,
        dwellSourcePreset: dwellVideo ? dwellSourcePreset : null,
        coremlCached: !!coremlCacheDirectory, rates }, null, 2),
      contentType: "application/json",
    });
    if (dockFixture) {
      await expect.poll(async () => {
        const status = await siteMonitoring();
        const worker = status.analyticsWorkers[canaryId];
        const latest = worker?.dockOccupancy?.[0];
        if (latest) dockReports.set(worker.reportedAt, latest);
        for (const { observation } of status.siteEvents) {
          if (observation?.sensorId === canaryId && observation.analytics?.ruleId === specialRule.id &&
              observation.analytics?.source === "live" && modelMatches(canaryId, observation.analytics.model)) dockEvents.add(observation.id);
        }
        const all = [...dockReports.values()];
        return { vacant: all.some(({ state }) => state === "vacant"),
          linkedArrival: all.some(({ state, arrivalEventId }) => state === "occupied" && !!arrivalEventId && dockEvents.has(arrivalEventId)) };
      }, { timeout: 48_000, intervals: [1000] }).toEqual({ vacant: true, linkedArrival: true });
      await testInfo.attach("Live dock snapshots and persisted arrivals", {
        body: JSON.stringify({ dockCamera: canaryId, snapshots: [...dockReports.values()], arrivals: [...dockEvents] }, null, 2),
        contentType: "application/json",
      });
    }

    if (dwellCycles) {
      const metadata = await runProcess("ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
        "-of", "csv=p=0", approach]);
      const period = Number(metadata.stdout.trim()) * 1000;
      expect(period).toBeGreaterThan(24_000);
      expect(period).toBeLessThan(26_000);
      const after = Date.now();
      const sources = [...clocks].map(([id, clock]) => {
        const epoch = clock();
        return { id, first: epoch + Math.ceil((after - epoch) / period) * period, epoch, clock };
      });
      const deadline = Math.max(...sources.map(({ first }) => first)) + dwellCycles * period + 1000;
      await expect.poll(() => Date.now(), { timeout: (dwellCycles + 1) * period + 6000, intervals: [500] }).toBeGreaterThan(deadline);
      const status = await siteMonitoring();
      const cycleResults = sources.map(({ id, first, epoch, clock }) => {
        const events = (status.siteEvents as { observation?: ReplayObservation }[]).flatMap(({ observation }) =>
          observation?.sensorId === id && observation.analytics?.source === "live" && modelMatches(id, observation.analytics.model) &&
          matchesDoorwayDwell(observation) ? [observation] : []);
        const cycles = Array.from({ length: dwellCycles }, (_, index) => {
          const start = first + index * period;
          const times = events.map((event) => (Date.parse(event.occurredAt) - start) / 1000);
          return times.filter((time) => time >= 14 && time < period / 1000);
        });
        return { id, first, clockShiftMs: clock() - epoch, cycles, fps: status.analyticsWorkers[id]?.fps };
      });
      const unready = cameras.filter(({ id }) => {
        const worker = status.analyticsWorkers[id];
        return worker?.state !== "running" || !modelMatches(id, worker.models[0]) || worker.pending !== 0 ||
          worker.fps < 3.5 || worker.frameAgeSeconds == null || worker.frameAgeSeconds >= 1 ||
          Date.now() / 1000 - worker.reportedAt > 20;
      }).map(({ id }) => id);
      const missingCrossings = cameras.filter(({ id }) => !specialCameraIds.has(id) &&
        !(status.siteEvents as { observation?: ReplayObservation }[]).some(({ observation }) =>
          observation?.sensorId === id && observation.eventType === "line-crossing" && observation.analytics?.source === "live" &&
          modelMatches(id, observation.analytics.model) && Date.parse(observation.occurredAt) >= after)).map(({ id }) => id);
      await testInfo.attach("Dwell observations per replay cycle", {
        body: JSON.stringify({ periodMs: period, failures: cycleResults.filter(({ cycles }) => cycles.some((times) => times.length !== 1)),
          cameras: cycleResults, unready, missingCrossings }, null, 2), contentType: "application/json",
      });
      expect.soft(unready, "Cameras reported healthy inference at the end of the cycle test").toEqual([]);
      expect.soft(missingCrossings, "Entrance cameras reported a live crossing during the cycle test").toEqual([]);
      expect.soft(cycleResults.filter(({ clockShiftMs }) => Math.abs(clockShiftMs) > 1000), "Publisher clocks stayed within one second").toEqual([]);
      expect.soft(cycleResults.filter(({ cycles }) => cycles.some((times) => times.length !== 1)), "Exactly one matching dwell per camera and loop").toEqual([]);
    }

    if (stairs) await verifyStairCrossings(testInfo, clocks, siteMonitoring, modelMatches);

    await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
    await page.goto(baseURL);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    await button(rail, "Posture").click();
    const posture = page.getByRole("region", { name: "Posture", exact: true });
    await posture.locator("summary", { hasText: "Shared site operator access" }).click();
    await posture.getByLabel("Shared site gateway URL").fill(gateway);
    await posture.getByLabel("Site operator token").fill(admin);
    await button(posture, "Connect operator").click();
    const analytics = posture.getByText("CCTV analytics", { exact: true });
    await analytics.click();
    const cameraRows = analytics.locator("..").getByRole("button").filter({ hasText: /^Camera \d\d/ });
    await expect(cameraRows).toHaveCount(cameraCount);
    const baseCanaryId = "yolo11:" + canaryModel.sha256.slice(0, 16) + (rotateCanary ? ":r90" : "");
    const detailSuffix = stairs || ruleDetailCanary ? ":rule-detail:" : canary || stopVideo || dockFixture ? ":detail:" : "";
    if (nativeFixture) {
      await expect(cameraRows.last()).toContainText("Camera-native events");
      await posture.getByText("Camera-native analytics", { exact: true }).click();
      const panel = posture.getByRole("region", { name: "Camera-native analytics" });
      await expect(panel.getByRole("list", { name: "Native camera events" }).getByRole("listitem")).toHaveCount(2);
    } else {
      await expect(cameraRows.last()).toContainText(baseCanaryId + detailSuffix);
    }
    if (dockFixture) {
      await expect(posture.getByRole("region", { name: "Dock occupancy" })).toContainText("Camera 12 · Bay occupancy");
    }
    if (stairs) {
      await expect(cameraRows.filter({ hasText: baseCanaryId + detailSuffix })).toHaveCount(cameraCount);
      await button(rail, "Alerts").click();
      const alert = page.getByRole("region", { name: "Alerts", exact: true })
        .getByRole("button", { name: /^Person crossed line.*School stairs crossing/ }).first();
      await alert.click();
      const evidence = page.getByRole("region", { name: "CCTV analytics evidence" });
      await expect(evidence).toContainText("School stairs crossing");
      await expect(evidence).toContainText("On-premises");
    }
    if (ruleDetailCanary) {
      const wideDraft = await update({ operation: "save", publishedVersion: 1, draftRevision: publishedRevision, configuration: {
        ...configuration,
        sensors: configuration.sensors.map((sensor) => sensor.id === canaryId ? { ...sensor, analyticsRules: [{
          ...sensor.analyticsRules[0], kind: "zone-entry", points: [[0, 0], [1, 0], [1, 1], [0, 1]],
        }] } : sensor),
      } });
      expect(wideDraft.status, await wideDraft.clone().text()).toBe(200);
      const widePublish = await update({ operation: "publish", publishedVersion: 1, draftRevision: (await wideDraft.json()).draftRevision,
        message: "Broaden the canary camera rule" });
      expect(widePublish.status).toBe(200);
      await expect.poll(async () => {
        const status = await siteMonitoring();
        const worker = status.analyticsWorkers[canaryId];
        return worker?.version === 2 && worker?.state === "running" && worker.models[0] === baseCanaryId &&
          status.siteEvents.some((row: { observation: { sensorId: string; analytics?: { model?: string; policyVersion?: number } } }) =>
            row.observation.sensorId === canaryId && row.observation.analytics?.policyVersion === 2 &&
              row.observation.analytics.model === baseCanaryId);
      }, { timeout: 26_000 }).toBe(true);
      await expect(cameraRows.last()).not.toContainText(":rule-detail:");
      await expect(cameraRows.last()).toContainText(baseCanaryId);
    }
  } finally {
    await Promise.all(children.reverse().map(stop));
    nativeFixture?.close();
    await inferenceFixture?.close();
    await rm(directory, { recursive: true, force: true });
  }
}
