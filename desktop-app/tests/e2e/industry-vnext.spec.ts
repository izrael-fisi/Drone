import { expect, test, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createServer as reservePort } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { onvifEventsFixture } from "./fixtures/onvif-events";

const gateway = fileURLToPath(new URL("../../../services/camera-gateway/gateway.py", import.meta.url));
const opcAdapter = fileURLToPath(new URL("../../../services/camera-gateway/inspection_opcua.py", import.meta.url));
const opcFixture = fileURLToPath(new URL("../../../tests/fixtures/opcua_station.py", import.meta.url));
const fixtureRecording = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const admin = "1".repeat(64), technician = "2".repeat(64);
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
const camera = { id: "camera", name: "Gate camera", modality: "vision", position: [-122.30, 37.78] };
const boundary = { id: "yard", name: "Yard gate", kind: "Access", sensorIds: ["camera"], enabled: true,
  vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] };
const wire = { id: "entry", name: "Entrance tripwire", kind: "line-crossing", object: "person", mode: "observe", boundaryId: "yard",
  points: [[.52, .5], [.52, 1]], direction: "negative", minimumScore: .25, minimumPixels: 12, seconds: .5 };

async function boot(directory: string, options: string[], port = "0") {
  const state = join(directory, "state"), roles = join(directory, "roles.json");
  await mkdir(state, { recursive: true });
  await writeFile(roles, JSON.stringify({ site: "Fixture site", siteId: "fixture-site", organization: { id: "fixture", name: "Fixture" },
    operators: { admin: { name: "Supervisor", role: "org-admin", cameras: "*", tokenSha256: hash(admin) },
      tech: { name: "Technician", role: "operator", cameras: ["camera"], tokenSha256: hash(technician) } } }));
  const child = spawn("python3", [gateway, "--port", port, "--state-dir", state, "--allow-source", "127.0.0.1/32", "--site-access", roles, ...options],
    { stdio: ["ignore", "pipe", "pipe"] });
  try {
    const address = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => finish(new Error(output || "Gateway startup timed out")), 8000);
      const receive = (chunk: Buffer) => {
        output += chunk.toString();
        const match = output.match(/Camera gateway listening on 127\.0\.0\.1:(\d+)/);
        if (match) finish(undefined, "http://127.0.0.1:" + match[1]);
      };
      const exited = () => finish(new Error(output || "Gateway exited"));
      function finish(error?: Error, address?: string) {
        clearTimeout(timer); child.stdout?.off("data", receive); child.stderr?.off("data", receive); child.off("exit", exited);
        if (error) reject(error); else resolve(address!);
      }
      child.stdout?.on("data", receive); child.stderr?.on("data", receive); child.once("exit", exited);
    });
    return { child, address, state };
  } catch (error) {
    await stop(child);
    throw error;
  }
}

async function stop(child?: ChildProcess) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  const timeout = setTimeout(() => child.kill("SIGKILL"), 2500);
  try { await once(child, "exit"); } finally { clearTimeout(timeout); }
}

function waitForProcess(child: ChildProcess, pattern: RegExp) {
  return new Promise<RegExpMatchArray>((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => done(), 12_000);
    function done(match?: RegExpMatchArray) {
      clearTimeout(timer); child.stdout?.off("data", chunk); child.stderr?.off("data", chunk); child.off("exit", exit);
      if (match) resolve(match); else reject(new Error(output || "Fixture process failed to start"));
    }
    function exit() { done(); }
    function chunk(data: Buffer) {
      output += data.toString();
      const match = output.match(pattern);
      if (match) done(match);
    }
    child.stdout?.on("data", chunk); child.stderr?.on("data", chunk); child.once("exit", exit);
  });
}

async function api(address: string, route: string, body?: unknown, token = admin) {
  const response = await fetch(address + "/api/site/" + route, { headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    ...(body !== undefined && { method: "POST", body: JSON.stringify(body) }) });
  const value = await response.json();
  expect(response.status, JSON.stringify(value)).toBe(200);
  return value;
}

async function configuration(address: string, operation: string, fields: object) {
  const { publishedVersion, draftRevision } = await api(address, "configuration");
  return api(address, "configuration", { operation, publishedVersion, draftRevision, ...fields });
}

async function connect(page: Page, base: string, address: string, token = admin) {
  await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
  await page.goto(base);
  await page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: "Posture", exact: true }).click();
  const posture = page.getByRole("region", { name: "Posture", exact: true });
  await posture.getByText("Shared site operator access", { exact: true }).click();
  await posture.getByLabel("Shared site gateway URL").fill(address);
  await posture.getByLabel("Site operator token").fill(token);
  await posture.getByRole("button", { name: "Connect operator" }).click();
  await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
  return posture;
}

test("tripwire commissioning compares retained video and shares reviewed evidence", async ({ browser, baseURL }) => {
  const python = process.env.PROXIGO_CCTV_PYTHON, model = process.env.PROXIGO_CCTV_YOLO11, video = process.env.PROXIGO_CCTV_LOAD_VIDEO;
  test.skip(!python || !model || !video, "Set the pinned YOLO11 and G326 fixtures.");
  test.setTimeout(100_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-commissioning-qa-"));
  const worker = join(directory, "worker.json"), cameraKey = "c".repeat(48);
  const source = join(directory, "recorder.mkv");
  const clipTimestamp = Date.now() - 25_000;
  const iso = new Date(clipTimestamp).toISOString();
  let instance: Awaited<ReturnType<typeof boot>> | undefined;
  let first: Page | undefined, second: Page | undefined;
  const recorder = createServer();
  try {
    execFileSync("ffmpeg", ["-v", "error", "-i", video!, "-t", "8", "-an", "-vf", "fps=4,scale=640:360",
      "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", source]);
    const media = await readFile(source);
    recorder.on("request", (request, response) => {
      if (request.url?.includes("list.cgi")) {
        response.end(`<root><recording recordingid="g326" diskid="SD" starttime="${new Date(clipTimestamp - 4000).toISOString()}" stoptime="${new Date(clipTimestamp + 4000).toISOString()}"/></root>`);
      } else if (request.url?.includes("exportrecording.cgi")) response.end(media);
      else response.writeHead(404).end();
    });
    recorder.listen(0, "127.0.0.1"); await once(recorder, "listening");
    await writeFile(worker, JSON.stringify({ python, model: { path: model, sha256: hash(await readFile(model!)),
      provider: process.env.PROXIGO_CCTV_YOLO11_PROVIDER ?? "CPUExecutionProvider" } }));
    await mkdir(join(directory, "state"));
    await writeFile(join(directory, "state", "cameras.json"), JSON.stringify({ camera: { key: cameraKey, url: "rtsp://127.0.0.1/fixture" } }));
    instance = await boot(directory, ["--commissioning-worker", worker, "--axis-recorder", "http://127.0.0.1:" + (recorder.address() as { port: number }).port]);
    const { address } = instance;
    const sensor = { ...camera, cameraGatewayKind: "rtsp", cameraFeedUrl: address + "/streams/" + cameraKey + "/index.m3u8" };
    await configuration(address, "save", { configuration: { sensors: [{ ...sensor, analyticsRules: [wire] }], boundaries: [boundary] } });
    await configuration(address, "publish", { message: "Publish entrance rule" });
    await configuration(address, "save", { configuration: { sensors: [{ ...sensor, analyticsRules: [{ ...wire, minimumScore: .99 }] }], boundaries: [boundary] } });
    const panel = async (page: Page, token: string) => {
      const posture = await connect(page, baseURL!, address, token);
      await posture.getByText("CCTV analytics", { exact: true }).click();
      await posture.getByText("Commission a tripwire", { exact: true }).click();
      return posture.getByRole("region", { name: "Tripwire commissioning" });
    };
    first = await browser.newPage();
    const ui = await panel(first, admin);
    await ui.getByLabel("Commissioning camera").selectOption("camera");
    await ui.getByLabel("Directional tripwire").selectOption("entry");
    await ui.getByLabel("Recording time (UTC)").fill(iso);
    await ui.getByRole("button", { name: "Find recording" }).click();
    await expect(ui.locator("video")).toBeVisible({ timeout: 25_000 });
    await ui.getByRole("button", { name: "Create comparison" }).click();
    const annotation = async (time: string) => {
      await ui.getByLabel("Crossing time (seconds)").fill(time);
      await ui.getByRole("button", { name: "Add expected crossing" }).click();
    };
    await annotation("1");
    await annotation("4.901");
    await ui.getByRole("button", { name: "Save reviewed interval" }).click();
    await expect(ui.getByText("Reviewed interval saved.")).toBeVisible();
    await ui.getByRole("button", { name: "Run comparison" }).click();
    const published = ui.getByRole("region", { name: "published results" });
    await expect(published).toContainText("1 matched · 1 missed · 0 extra", { timeout: 42_000 });
    await expect(ui.getByRole("region", { name: "candidate results" })).toContainText("0 matched · 2 missed · 0 extra");
    await published.getByRole("button", { name: /^Match at/ }).click();
    await expect.poll(() => ui.locator("video").evaluate((node: HTMLVideoElement) => node.currentTime)).toBeGreaterThan(3.8);
    second = await browser.newPage();
    const remote = await panel(second, technician);
    const [saved] = (await api(address, "commissioning-runs", undefined, technician)).runs;
    await remote.getByLabel("Commissioning run").selectOption(saved.id);
    await expect(remote.getByRole("region", { name: "published results" })).toContainText("1 matched · 1 missed · 0 extra");
    await expect(remote.getByRole("button", { name: "Save reviewed interval" })).toHaveCount(0);
    await ui.getByRole("button", { name: "Remove crossing at 4.901 s" }).click();
    await ui.getByRole("button", { name: "Save reviewed interval" }).click();
    await expect(ui.getByText("Reviewed interval saved.")).toBeVisible();
    await ui.getByRole("button", { name: "Run comparison" }).click();
    await expect(published).toContainText("0 matched · 1 missed · 1 extra", { timeout: 42_000 });
  } finally {
    await first?.close(); await second?.close(); await stop(instance?.child);
    recorder.closeAllConnections(); recorder.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("shared dock schedule persists published revisions and scoped check-ins", async ({ browser, baseURL }) => {
  test.setTimeout(65_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-yard-qa-"));
  const tokens = join(directory, "workers.json"), worker = "3".repeat(64), route = "dock-schedules";
  const dock = { ...wire, id: "dock", kind: "zone-entry", object: "vehicle", direction: "either", trackOccupancy: true, seconds: 2,
    points: [[.2, .2], [.8, .2], [.8, .8], [.2, .8]] };
  const pages: Page[] = [];
  let instance: Awaited<ReturnType<typeof boot>> | undefined;
  try {
    await writeFile(tokens, JSON.stringify({ camera: { "on-premises": worker } }));
    instance = await boot(directory, ["--analytics-workers", tokens]);
    const { address } = instance;
    await configuration(address, "save", { configuration: { sensors: [{ ...camera, analyticsPlacement: "on-premises", analyticsRules: [dock] }], boundaries: [boundary] } });
    const published = await configuration(address, "publish", { message: "Publish dock region" });
    const { day } = await api(address, route);
    const start = new Date(Date.parse(day + "T00:00:00Z")).toISOString();
    const arrivalAt = Date.now();
    const end = new Date(arrivalAt + 700).toISOString();
    const arrival = "cctv-" + "b".repeat(32);
    const csv = (id: string) => "appointmentId,boundaryId,startsAt,endsAt\n" + `${id},yard,${start},${end}`;
    await api(address, "telemetry", { type: "observation", sensorId: "camera", id: arrival, eventType: "zone-entry", summary: "Dock",
      occurredAt: new Date(arrivalAt).toISOString(), analytics: { ruleId: "dock", policyVersion: published.publishedVersion,
        object: "vehicle", score: .9, box: [.3, .3, .5, .6], frame: [640, 480], source: "live", trackId: "vehicle-track", model: "yolo11-fixture" } }, worker);
    const first = await browser.newPage(); pages.push(first);
    const posture = await connect(first, baseURL!, address);
    await posture.getByText("Shared dock schedule", { exact: true }).click();
    const schedule = posture.getByRole("region", { name: "Shared dock schedule" });
    await schedule.getByLabel("Dock appointments CSV").setInputFiles({ name: "shift.csv", mimeType: "text/csv", buffer: Buffer.from(csv("truck-1")) });
    await expect(schedule.getByRole("region", { name: "Schedule preview" })).toContainText("truck-1");
    await schedule.getByRole("button", { name: "Publish schedule" }).click();
    await expect(schedule).toContainText("truck-1 · Arrived");
    const current = await api(address, route);
    expect(await api(address, route, { operation: "preview", day, csv: csv("truck-1") })).toMatchObject({ revision: current.revision, published: true });

    const push = () => fetch(address + "/api/cameras/camera/analytics", { method: "POST", headers: { Authorization: "Bearer " + worker, "Content-Type": "application/json" },
      body: JSON.stringify({ state: "running", version: 1, models: ["yolo11-fixture"], pending: 0, fps: 4, frameAgeSeconds: .1,
        dockOccupancy: [{ ruleId: "dock", state: "occupied", sampledAt: Date.now() / 1000, changedAt: arrivalAt / 1000, arrivalEventId: arrival }] }) });
    expect((await push()).status).toBe(200);
    await expect(schedule).toContainText("truck-1 · Overstay");
    const second = await browser.newPage(); pages.push(second);
    const remotePosture = await connect(second, baseURL!, address, technician);
    await remotePosture.getByText("Shared dock schedule", { exact: true }).click();
    const remote = remotePosture.getByRole("region", { name: "Shared dock schedule" });
    await remote.getByLabel("Check-in bay").selectOption("yard");
    await remote.getByRole("combobox", { name: "Appointment", exact: true }).selectOption("truck-1");
    await remote.getByLabel("Shipment or appointment reference").fill("Shipment EX-221");
    await remote.getByRole("button", { name: "Save check-in" }).click();
    await expect(schedule).toContainText("Shipment EX-221", { timeout: 10_000 });
    await stop(instance.child);
    instance = await boot(directory, ["--analytics-workers", tokens], new URL(address).port);
    expect((await api(address, route, undefined, technician)).revision).toBe(current.revision);
    await expect(remote).toContainText("Shipment EX-221");
    await expect(schedule.getByText("Yard gate · Unknown", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(schedule).toContainText("truck-1 · Arrived");
  } finally {
    await Promise.all(pages.map((page) => page.close()));
    await stop(instance?.child);
    await rm(directory, { recursive: true, force: true });
  }
});

test("door passage review preserves ordered evidence across a gateway restart", async ({ browser, baseURL }) => {
  test.setTimeout(65_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-passage-qa-"));
  const adapters = join(directory, "devices.json"), workers = join(directory, "workers.json");
  const adapter = "3".repeat(64), worker = "4".repeat(64);
  const options = ["--telemetry-devices", adapters, "--analytics-workers", workers];
  const pages: Page[] = [];
  let instance: Awaited<ReturnType<typeof boot>> | undefined;
  try {
    await writeFile(adapters, JSON.stringify({ door: adapter }));
    await writeFile(workers, JSON.stringify({ camera: { "on-premises": worker } }));
    instance = await boot(directory, options);
    const { address } = instance;
    const door = { id: "door", name: "East door", modality: "access", position: camera.position, linkedCameraId: "camera", passageRuleId: "entry" };
    await configuration(address, "save", { configuration: { sensors: [{ ...camera, analyticsPlacement: "on-premises", analyticsRules: [wire] }, door],
      boundaries: [{ ...boundary, sensorIds: ["camera", "door"] }] } });
    await configuration(address, "publish", { message: "Publish passage review" });
    const heartbeat = () => fetch(address + "/api/cameras/camera/analytics", { method: "POST", headers: { Authorization: "Bearer " + worker, "Content-Type": "application/json" },
      body: JSON.stringify({ state: "running", version: 1, models: ["yolo11-fixture"], pending: 0, fps: 4, frameAgeSeconds: .1 }) });
    expect((await heartbeat()).status).toBe(200);
    const now = Date.now() + 20;
    const fact = (id: string, eventType: string, offset: number, summary: string, trackId?: string) => api(address, "telemetry",
      { type: "observation", id, sensorId: trackId ? "camera" : "door", eventType, summary, occurredAt: new Date(now + offset).toISOString(),
        ...(trackId && { analytics: { ruleId: "entry", policyVersion: 1, object: "person", score: .94, box: [.3, .25, .6, .65],
          frame: [640, 480], source: "live", trackId, model: "yolo11-fixture" } }) }, trackId ? worker : adapter);
    await fact("grant", "access-granted", 0, "Access granted");
    await fact("opened", "door-opened", 80, "East door opened");
    await fact("closed", "door-closed", 600, "East door closed");
    await fact("person2", "line-crossing", 400, "Second crossing", "track-two");
    await fact("person1", "line-crossing", 200, "First crossing", "track-one");
    await expect.poll(() => Date.now()).toBeGreaterThan(now + 650);
    expect((await heartbeat()).status).toBe(200);
    expect((await api(address, "door-passages")).episodes).toHaveLength(0);
    await stop(instance.child);
    instance = await boot(directory, options, new URL(address).port);
    for (const token of [admin, technician]) {
      const page = await browser.newPage(); pages.push(page);
      await connect(page, baseURL!, address, token);
      await page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: "Alerts", exact: true }).click();
      const alerts = page.getByRole("region", { name: "Alerts", exact: true });
      await alerts.getByText("Door passage review", { exact: true }).click();
      const review = alerts.getByRole("region", { name: "Door passage review" });
      await review.getByRole("button", { name: /^Possible extra passage/ }).click({ timeout: 24_000 });
      await expect(review.getByRole("list", { name: "Replay timeline" }).locator("strong")).toHaveText([
        "Access granted", "East door opened", "Person crossed line · Entrance tripwire", "Person crossed line · Entrance tripwire", "East door closed",
      ]);
      await review.getByRole("button", { name: /East door closed/ }).click();
      await expect(review.getByRole("region", { name: "Selected replay event" })).toContainText("East door closed");
      await expect(alerts).toContainText("0 matching incidents");
    }
  } finally {
    await Promise.all(pages.map((page) => page.close()));
    await stop(instance?.child);
    await rm(directory, { recursive: true, force: true });
  }
});

test("camera-native ONVIF subscribes, deduplicates and shows scoped status in Chrome", async ({ page, baseURL }) => {
  test.setTimeout(65_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-native-qa-"));
  const fixture = await onvifEventsFixture();
  const config = join(directory, "onvif-events.json"), workers = join(directory, "workers.json"), token = "5".repeat(64);
  let instance: Awaited<ReturnType<typeof boot>> | undefined;
  try {
    fixture.configure({ supported: false });
    await writeFile(config, JSON.stringify([{ cameraId: "camera", address: fixture.address, ruleId: "entry",
      deviceRule: "fixture-line", videoSourceToken: "fixture-video" }]));
    await writeFile(workers, JSON.stringify({ camera: { "on-premises": token } }));
    instance = await boot(directory, ["--onvif-analytics", config, "--analytics-workers", workers]);
    const { address } = instance;
    await configuration(address, "save", { configuration: { sensors: [{ ...camera, analyticsPlacement: "on-premises",
      analyticsRules: [{ ...wire, cameraNative: true }] }], boundaries: [boundary] } });
    await configuration(address, "publish", { message: "Publish native camera rule" });
    const worker = await fetch(address + "/api/cameras/camera/analytics", { headers: { Authorization: "Bearer " + token } });
    expect((await worker.json()).rules).toEqual([]);
    const posture = await connect(page, baseURL!, address, technician);
    await posture.getByText("CCTV analytics", { exact: true }).click();
    await posture.getByText("Camera-native analytics", { exact: true }).click();
    const panel = posture.getByRole("region", { name: "Camera-native analytics" });
    await expect(panel).toContainText("Gate camera · Unsupported", { timeout: 12_000 });
    fixture.configure({ supported: true });
    await expect(panel).toContainText("Gate camera · Supported", { timeout: 15_000 });
    const at = fixture.announce(1);
    fixture.announce(1, at);
    fixture.announce(3, new Date(Date.now() + 20_000).toISOString());
    const rows = panel.getByRole("list", { name: "Native camera events" }).getByRole("listitem");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("ONVIF Profile M");
    await expect.poll(() => fixture.stats().subscriptions).toBeGreaterThan(1);
    fixture.configure({ available: false, clockOffset: 15 });
    await expect(panel).toContainText("Gate camera · Disconnected");
    fixture.configure({ available: true });
    await expect(panel).toContainText("Check the camera clock", { timeout: 12_000 });
    fixture.configure({ clockOffset: 0 });
    fixture.announce(2);
    await expect(rows).toHaveCount(2, { timeout: 12_000 });
    const scope = await api(address, "camera-native", undefined, technician);
    expect(scope.events.map((item: { observation: { nativeAnalytics: { objectId: string } } }) => item.observation.nativeAnalytics.objectId)).toEqual(["2", "1"]);
    await page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: "Alerts", exact: true }).click();
    await expect(page.getByRole("region", { name: "Alerts", exact: true })).toContainText("0 matching incidents");
  } finally {
    await stop(instance?.child);
    fixture.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("OPC UA inspection results survive reconnect and export scoped filtered evidence", async ({ page, baseURL }) => {
  const python = process.env.PROXIGO_OPCUA_PYTHON;
  test.skip(!python, "Set PROXIGO_OPCUA_PYTHON to the optional OPC UA environment.");
  test.setTimeout(90_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-opc-qa-"));
  const devices = join(directory, "devices.json"), token = "7".repeat(64), cameraKey = "f".repeat(48);
  const portSocket = reservePort();
  portSocket.listen(0, "127.0.0.1"); await once(portSocket, "listening");
  const port = (portSocket.address() as { port: number }).port;
  await new Promise<void>((resolve) => portSocket.close(() => resolve()));
  const clip = join(directory, "recorder.mkv");
  execFileSync("ffmpeg", ["-v", "error", "-i", fixtureRecording, "-c", "copy", clip]);
  const media = await readFile(clip);
  const recorder = createServer((request, response) => {
    const source = new URL(request.url ?? "/", "http://127.0.0.1");
    if (source.pathname.endsWith("/list.cgi")) {
      const start = Date.parse(source.searchParams.get("starttime") ?? "");
      response.end(`<root><recording recordingid="station" diskid="SD" starttime="${new Date(start).toISOString()}" stoptime="${new Date(start + 4000).toISOString()}"/></root>`);
    } else if (source.pathname.endsWith("/exportrecording.cgi")) response.end(media);
    else response.writeHead(404).end();
  });
  let instance: Awaited<ReturnType<typeof boot>> | undefined, station: ChildProcess | undefined, adapter: ChildProcess | undefined;
  try {
    recorder.listen(0, "127.0.0.1"); await once(recorder, "listening");
    await writeFile(devices, JSON.stringify({ camera: token }));
    await writeFile(join(directory, "station.token"), token, { mode: 0o600 });
    await mkdir(join(directory, "state"));
    await writeFile(join(directory, "state", "cameras.json"), JSON.stringify({ camera: { key: cameraKey, url: "rtsp://127.0.0.1/station" } }));
    instance = await boot(directory, ["--telemetry-devices", devices, "--axis-recorder", "http://127.0.0.1:" + (recorder.address() as { port: number }).port]);
    const { address } = instance;
    await configuration(address, "save", { configuration: { sensors: [{ ...camera, inspectionStationId: "line-two",
      cameraGatewayKind: "rtsp", cameraFeedUrl: address + "/streams/" + cameraKey + "/index.m3u8" }], boundaries: [boundary] } });
    await configuration(address, "publish", { message: "Enroll fixture station" });
    station = spawn(python!, [opcFixture, "--port", String(port)], { stdio: ["pipe", "pipe", "pipe"] });
    const [, eventType] = await waitForProcess(station, /OPC fixture ready (\S+)/);
    const adapterConfig = join(directory, "opc-station.json");
    await writeFile(adapterConfig, JSON.stringify({ cameraId: "camera", stationId: "line-two", gateway: address, tokenFile: "station.token",
      outbox: "outbox.sqlite3", endpoint: `opc.tcp://127.0.0.1:${port}/fixture/`, eventType, loopbackFixture: true,
      outcomes: { passed: "pass", failed: "fail", unfinished: "incomplete" },
      fields: { resultId: "ResultId", occurredAt: "Time", recipeId: "RecipeId", componentId: "ComponentId",
        partId: "PartId", lotId: "LotId", outcome: "Outcome", simulated: "IsSimulated", partial: "IsPartial" } }));
    const start = async () => {
      adapter = spawn(python!, [opcAdapter, "--config", adapterConfig], { stdio: ["ignore", "pipe", "pipe"] });
      await waitForProcess(adapter, /OPC UA inspection subscription ready/);
    };
    await start();
    const emit = (id: string, outcome: string, fields = {}) => {
      station!.stdin!.write(JSON.stringify({ id, outcome, ...fields }) + "\n");
    };
    emit("pass-001", "passed", { lot: "lot-b" });
    emit("fail-001", "failed", { part: "frame-002" });
    emit("incomplete-001", "unfinished");
    emit("partial-001", "failed", { partial: true });
    await expect.poll(async () => (await api(address, "inspection-results")).entries.length, { timeout: 12_000 }).toBe(3);
    await stop(adapter);
    await start();
    emit("fail-001", "failed", { part: "frame-002" });
    emit("pass-002", "passed", { recipe: "bracket-v5", lot: "lot-b" });
    await expect.poll(async () => (await api(address, "inspection-results")).entries.length, { timeout: 12_000 }).toBe(4);
    const posture = await connect(page, baseURL!, address, technician);
    await posture.getByText("Inspection results", { exact: true }).click();
    const panel = posture.getByRole("region", { name: "Inspection results", exact: true });
    await expect(panel.getByRole("button", { name: /Inspection incomplete/ })).toBeVisible();
    await panel.getByLabel("Recipe ID").fill("bracket-v4");
    await panel.getByLabel("Lot ID").fill("lot-a");
    await panel.getByRole("button", { name: "Apply inspection filters" }).click();
    await panel.getByLabel("Inspection outcome").selectOption("fail");
    await panel.getByRole("button", { name: /Component presence failed/ }).click();
    const detail = panel.getByRole("region", { name: "Selected inspection result" });
    await expect(detail).toContainText("OPC UA · Simulated station · Result fail-001");
    await expect(detail).toContainText("Part frame-002 · Lot lot-a");
    const download = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Export filtered results" }).click();
    const content = await readFile((await (await download).path())!, "utf8");
    expect(content).toContain("fail-001,bracket-v4,retaining-clip,frame-002,lot-a,fail,opc-ua");
    expect(content.trim().split("\n")).toHaveLength(2);
    await detail.getByRole("button", { name: "Find station recording" }).click();
    await expect(detail.locator("video")).toBeVisible({ timeout: 22_000 });
    await expect.poll(() => detail.locator("video").evaluate((node: HTMLVideoElement) => node.videoWidth)).toBeGreaterThan(0);
  } finally {
    await stop(adapter); await stop(station); await stop(instance?.child);
    recorder.closeAllConnections(); recorder.close();
    await rm(directory, { recursive: true, force: true });
  }
});
