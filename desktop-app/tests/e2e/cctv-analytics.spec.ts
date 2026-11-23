import { expect, test, type Locator, type Page } from "@playwright/test";
import { execFile, execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer, request } from "node:http";
import { createServer as createTlsServer } from "node:https";
import { connect as connectSocket, createServer as reservePort, type Socket } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const gatewayScript = fileURLToPath(new URL("../../../services/camera-gateway/gateway.py", import.meta.url));
const workerScript = fileURLToPath(new URL("../../../services/camera-gateway/analytics_worker.py", import.meta.url));
const recorderClip = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const python = process.env.PROXIGO_CCTV_PYTHON, ground = process.env.PROXIGO_CCTV_YOLOX, drone = process.env.PROXIGO_CCTV_OWLV2;
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

async function stop(child: ChildProcess) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  const done = once(child, "exit");
  child.kill();
  const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
  try { await done; } finally { clearTimeout(timer); }
}

test("cloud and on-premises CCTV workers produce incidents and follow published compute placement", async ({ page, baseURL }) => {
  test.skip(![python, ground, drone, groundImage, droneImage, mediaMtx].every(Boolean), "Configure the local analytics models, two fixture images, and MediaMTX.");
  test.setTimeout(150_000);
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
    const publish = (file: string, path: string, dimensions: string) => spawnReady(start("ffmpeg", ["-re", "-loop", "1", "-framerate", "4", "-i", file,
      "-vf", "scale=" + dimensions, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "8", "-pix_fmt", "yuv420p",
      "-f", "rtsp", "-rtsp_transport", "tcp", rtsp + "/" + path]), /Output #0, rtsp/);
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

    async function editRule(device: string, name: string, behavior: string, mode: string) {
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
      await details.getByLabel("Confirmation time (seconds)").fill("1");
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
    })]));
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
    const yardModel = { kind: "yolox", path: ground, fps: 4 };
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
    await startWorker("sky", droneToken, "drone", { kind: "owlv2", path: drone, fps: .3 }, 768, 512, "on-premises");
    await expect.poll(async () => {
      const status = await fetch(gateway + "/api/site/monitoring", { headers: adminHeaders }).then((response) => response.json());
      return status.alerts.some((alert: { observation: { eventType: string } }) => alert.observation.eventType === "restricted-stop");
    }, { timeout: 12_000 }).toBe(true);
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
    await alerts.getByRole("button", { name: /^Vehicle stopped in zone · No stopping/ }).first().click({ timeout: 18_000 });
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText("yolox:");
    await expect(inspector.getByRole("region", { name: "CCTV analytics evidence" })).toContainText("Cloud");
    await button(inspector, "Replay timeline").click();
    await expect(inspector.getByRole("region", { name: "Selected replay event" })).toContainText("No stopping");
    await button(inspector, "Close replay").click();
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
