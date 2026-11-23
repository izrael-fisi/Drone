import { expect, test, type Locator, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { createServer as reservePort } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const gatewayScript = fileURLToPath(new URL("../../../services/camera-gateway/gateway.py", import.meta.url));
const footage = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const mediaMtxBinary = process.env.MEDIAMTX;
const cameraUser = "fixture-reader";
const cameraPassword = "fixture-password";
const deviceNamespace = "http://www.onvif.org/ver10/device/wsdl";
const mediaNamespace = "http://www.onvif.org/ver20/media/wsdl";
const viewKey = "proxigo.global-view.view.v1";

async function listenPort(server: ReturnType<typeof reservePort> | ReturnType<typeof createServer>) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return (server.address() as { port: number }).port;
}

async function shutDown(child?: ChildProcess) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit");
  child.kill();
  const deadline = setTimeout(() => child.kill("SIGKILL"), 3000);
  try { await exited; } finally { clearTimeout(deadline); }
}

async function waitForOutput(child: ChildProcess, pattern: RegExp) {
  return new Promise<RegExpMatchArray>((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => finish(new Error(`Fixture did not emit ${pattern}. ${output}`)), 12_000);
    function finish(error?: Error, result?: RegExpMatchArray) {
      clearTimeout(timeout);
      child.stdout?.off("data", capture);
      child.stderr?.off("data", capture);
      child.off("error", finish);
      child.off("exit", onExit);
      if (error) reject(error);
      else if (result) resolve(result);
    }
    function capture(chunk: Buffer) {
      output += chunk.toString();
      const match = output.match(pattern);
      if (match) finish(undefined, match);
    }
    function onExit() { finish(new Error(`Fixture exited while waiting for ${pattern}. ${output}`)); }
    child.stdout?.on("data", capture);
    child.stderr?.on("data", capture);
    child.once("error", finish);
    child.once("exit", onExit);
  });
}

function soap(namespace: string, body: string) {
  return `<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:t="${namespace}"><s:Body>${body}</s:Body></s:Envelope>`;
}

function usernameTokenValid(body: string) {
  const text = (tag: string) => body.match(new RegExp(`<[^> ]+:${tag}(?: [^>]*)?>([^<]+)</[^>]+>`))?.[1];
  const nonce = text("Nonce");
  const created = text("Created");
  return nonce && created && text("Username") === cameraUser
    && text("Password") === createHash("sha1").update(Buffer.from(nonce, "base64")).update(created).update(cameraPassword).digest("base64");
}

const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });

test("register direct RTSP and authenticated ONVIF cameras, then play both in the wall", async ({ page, baseURL }) => {
  test.skip(!mediaMtxBinary, "Set MEDIAMTX to a MediaMTX binary to run the live gateway integration.");
  test.setTimeout(120_000);
  const data = await mkdtemp(join(tmpdir(), "proxigo-gateway-e2e-"));
  const socket = reservePort();
  const rtspPort = await listenPort(socket);
  await new Promise<void>((resolve) => socket.close(() => resolve()));
  const rtsp = `rtsp://127.0.0.1:${rtspPort}/camera`;
  const selectedProfiles: string[] = [];
  const archiveFile = join(data, "archive.mkv");
  execFileSync("ffmpeg", ["-v", "error", "-i", footage, "-c", "copy", archiveFile]);
  const onvif = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    if (url.pathname === "/axis-cgi/record/list.cgi") {
      const event = Date.parse(url.searchParams.get("starttime") ?? "") + 1000;
      response.setHeader("Content-Type", "application/xml");
      response.end(`<root><recordings><recording recordingid="fixture-recording" diskid="SD_DISK" starttime="${new Date(event - 2000).toISOString()}" stoptime="${new Date(event + 4000).toISOString()}"/></recordings></root>`);
      return;
    }
    if (url.pathname === "/axis-cgi/record/export/exportrecording.cgi") {
      response.setHeader("Content-Type", "video/x-matroska");
      response.end(await readFile(archiveFile));
      return;
    }
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    if (!usernameTokenValid(body)) {
      response.writeHead(401).end();
      return;
    }
    const action = request.headers["content-type"] ?? "";
    response.setHeader("Content-Type", "application/soap+xml");
    if (action.includes("/GetServices")) {
      const endpoint = `http://127.0.0.1:${(onvif.address() as { port: number }).port}/onvif/media2`;
      response.end(soap(deviceNamespace, `<t:GetServicesResponse><t:Service><t:Namespace>${mediaNamespace}</t:Namespace><t:XAddr>${endpoint}</t:XAddr></t:Service></t:GetServicesResponse>`));
    } else if (action.includes("/GetProfiles")) {
      response.end(soap(mediaNamespace, "<t:GetProfilesResponse><t:Profiles token=\"primary\"><t:Name>Primary</t:Name></t:Profiles><t:Profiles token=\"sub\"><t:Name>Lower bandwidth</t:Name></t:Profiles></t:GetProfilesResponse>"));
    } else if (action.includes("/GetStreamUri")) {
      const profile = body.match(/:ProfileToken>([^<]+)/)?.[1] ?? "";
      selectedProfiles.push(profile);
      response.end(soap(mediaNamespace, `<t:GetStreamUriResponse><t:Uri>${profile === "sub" ? rtsp + "-grid" : rtsp}</t:Uri></t:GetStreamUriResponse>`));
    } else response.writeHead(400).end();
  });
  let mt: ChildProcess | undefined;
  let publisher: ChildProcess | undefined;
  let gridPublisher: ChildProcess | undefined;
  let gateway: ChildProcess | undefined;
  try {
    const onvifPort = await listenPort(onvif);
    const config = join(data, "mediamtx.yml");
    await writeFile(config, `rtspAddress: 127.0.0.1:${rtspPort}
rtspTransports: [tcp]
rtmp: no
hls: no
webrtc: no
srt: no
moq: no
authInternalUsers:
  - user: any
    permissions:
      - action: publish
        path: camera
      - action: publish
        path: camera-grid
  - user: ${cameraUser}
    pass: ${cameraPassword}
    permissions:
      - action: read
        path: camera
      - action: read
        path: camera-grid
paths:
  camera:
  camera-grid:
`);
    mt = spawn(mediaMtxBinary!, [config], { cwd: data, stdio: ["ignore", "pipe", "pipe"] });
    await waitForOutput(mt, /\[RTSP\].*(?:listener opened|started with listeners)/);
    const publish = (path = rtsp) => spawn("ffmpeg", [
      "-hide_banner", "-loglevel", "info", "-re", "-stream_loop", "-1", "-i", footage, "-an",
      "-c:v", "libx264", "-r", "15", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "15", "-pix_fmt", "yuv420p",
      "-f", "rtsp", "-rtsp_transport", "tcp", path,
    ], { stdio: ["ignore", "ignore", "pipe"] });
    publisher = publish();
    await waitForOutput(publisher, /Output #0, rtsp/);
    gridPublisher = publish(rtsp + "-grid");
    await waitForOutput(gridPublisher, /Output #0, rtsp/);
    const state = join(data, "state");
    const startGateway = async (port: string) => {
      gateway = spawn("python3", [gatewayScript, "--port", port, "--state-dir", state, "--allow-source", "127.0.0.1/32",
        "--probe-interval", "5", "--axis-recorder", `http://127.0.0.1:${onvifPort}`], {
        stdio: ["ignore", "pipe", "pipe"],
      });
      const match = await waitForOutput(gateway, /Camera gateway listening on 127\.0\.0\.1:(\d+)/);
      return match[1];
    };
    const gatewayPort = await startGateway("0");
    const gatewayURL = `http://127.0.0.1:${gatewayPort}`;
    const token = (await readFile(join(state, "management-token"), "utf8")).trim();
    expect((await stat(join(state, "management-token"))).mode & 0o077).toBe(0);
    const rejected = await fetch(gatewayURL + "/health", { headers: { Origin: "https://outside.example" } });
    expect(rejected.status).toBe(403);

    await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
    await page.goto(baseURL!);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    const hub = page.getByRole("region", { name: "Cameras", exact: true });
    const editor = page.getByRole("complementary", { name: "Monitor and review" });

    async function startCamera(name: string, connection: "onvif" | "rtsp", address: string) {
      await button(rail, "Cameras").click();
      await button(hub, "Add camera").click();
      await editor.getByLabel("Device name").fill(name);
      await editor.getByLabel("Latitude").fill("37.79");
      await editor.getByLabel("Longitude").fill("-122.30");
      await editor.getByLabel("Connection type").selectOption(connection);
      await editor.getByLabel("Camera gateway URL").fill(gatewayURL);
      await editor.getByLabel("Gateway management token").fill(token);
      await editor.getByLabel(connection === "onvif" ? "ONVIF device address" : "RTSP stream URL").fill(address);
      await editor.getByLabel("Camera username").fill(cameraUser);
      await editor.getByLabel("Camera password").fill(cameraPassword);
    }

    await startCamera("ONVIF loading dock", "onvif", `http://127.0.0.1:${onvifPort}/onvif/device_service`);
    await editor.getByLabel("North perimeter", { exact: true }).check();
    await editor.getByLabel("Configure field of view").check();
    await editor.getByLabel("Horizontal FOV (°)").fill("360");
    await editor.getByLabel("Range (m)").fill("1200");
    await button(editor, "Find ONVIF profiles").click();
    await editor.getByLabel("ONVIF profile").selectOption("primary");
    await editor.getByLabel("Live wall profile").selectOption("sub");
    await button(editor, "Connect and save").click();
    await expect(editor.getByRole("heading", { name: "ONVIF loading dock", exact: true })).toBeFocused({ timeout: 35_000 });
    expect(selectedProfiles).toEqual(["primary", "sub"]);

    await startCamera("RTSP perimeter", "rtsp", "rtsp://192.0.2.10/camera");
    await button(editor, "Connect and save").click();
    await expect(editor.getByRole("alert")).toContainText("outside the gateway's allowed source networks");
    await editor.getByLabel("RTSP stream URL").fill(rtsp);
    await editor.getByLabel("Gateway management token").fill("wrong-token");
    await button(editor, "Connect and save").click();
    await expect(editor.getByRole("alert")).toContainText("management token");
    await editor.getByLabel("Gateway management token").fill(token);
    await button(editor, "Connect and save").click();
    await expect(editor.getByRole("heading", { name: "RTSP perimeter", exact: true })).toBeFocused({ timeout: 22_000 });
    expect((await stat(join(state, "cameras.json"))).mode & 0o077).toBe(0);
    const storage = await page.evaluate(() => JSON.stringify(localStorage));
    expect(storage).toContain("/grid/index.m3u8");
    for (const secret of [token, cameraUser, cameraPassword, rtsp]) expect(storage).not.toContain(secret);

    async function assertWall() {
      await button(rail, "Cameras").click();
      expect((await rail.boundingBox())!.x).toBeLessThan((await hub.boundingBox())!.x);
      for (const [name, type] of [["ONVIF loading dock", "ONVIF"], ["RTSP perimeter", "RTSP"]]) {
        const tile = hub.getByRole("article", { name });
        await expect(tile).toContainText(type === "ONVIF" ? "Lower-bandwidth feed" : "RTSP via gateway");
        await expect.poll(() => tile.locator("video").evaluate((video) => video.videoWidth), { timeout: 22_000 }).toBeGreaterThan(0);
        const video = tile.locator("video");
        const before = await video.evaluate((element) => element.currentTime);
        await expect.poll(() => video.evaluate((element) => element.currentTime)).toBeGreaterThan(before);
      }
    }
    await assertWall();
    await button(rail, "Site list").click();
    await expect(hub).toHaveCount(0);
    // Feed URLs must survive both a gateway restart and a browser reload.
    await shutDown(gateway);
    await startGateway(gatewayPort);
    await page.reload();
    await assertWall();

    await button(hub, "Focus ONVIF loading dock").click();
    const field = hub.getByRole("region", { name: "Camera configuration" });
    await button(field, "Mark view checked on site").click();
    await expect(field.locator("time")).toHaveCount(1);
    await field.locator("summary").filter({ hasText: "Gateway access and analytics" }).click();
    await field.getByLabel("Gateway management token").fill(token);
    await button(field, "Create camera viewer link").click();
    await expect(field.getByLabel("Viewer link", { exact: true })).toHaveValue(/camera-viewer#/);
    const viewer = await page.context().newPage();
    await viewer.goto(await field.getByLabel("Viewer link", { exact: true }).inputValue());
    await expect(viewer.getByRole("heading", { name: "Shared camera view" })).toBeVisible();
    await expect.poll(() => viewer.locator("video").evaluate((video) => video.currentTime), { timeout: 20_000 }).toBeGreaterThan(0);
    await expect(viewer.getByRole("navigation")).toHaveCount(0);
    await viewer.close();

    await field.getByLabel("Gateway management token").fill(token);
    await button(field, "Get camera event token").click();
    await expect(field.getByLabel("Camera event token")).toHaveValue(/[a-f0-9]{64}/);
    const eventToken = await field.getByLabel("Camera event token").inputValue();
    const eventURL = await field.getByLabel("Analytics endpoint").inputValue();
    const cameraId = eventURL.split("/").at(-2)!;
    const event = { id: "tamper-001", event: "tamper", occurredAt: new Date(Date.now() - 20_000).toISOString() };
    const headers = { Authorization: "Bearer " + eventToken, "Content-Type": "application/json" };
    const push = () => fetch(eventURL, { method: "POST", headers, body: JSON.stringify(event) });
    expect(await (await push()).json()).toEqual({ accepted: true });
    expect(await (await push()).json()).toEqual({ accepted: false });
    expect((await fetch(gatewayURL + "/api/cameras/viewer-link", { method: "POST", headers,
      body: JSON.stringify({ deviceId: cameraId, minutes: 10 }) })).status).toBe(401);
    await button(rail, "Alerts").click();
    await page.getByRole("button", { name: /Camera tampering.*North perimeter/ }).click();
    await expect(editor.getByText("Camera location", { exact: true })).toBeVisible();
    await button(editor, "Open incident cameras").click();
    await expect(editor.getByLabel("Incident camera")).toHaveValue(cameraId);
    await button(editor, "Event recording").click({ timeout: 22_000 });
    await expect.poll(() => editor.getByLabel("ONVIF loading dock recording feed").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(2);
    await editor.getByLabel("Operator name").fill("Fixture guard");
    await button(editor, "Take ownership").click();
    await editor.locator("summary").filter({ hasText: "Response checklist and handoff" }).click();
    await editor.getByLabel("Inspect live video for an obstructed or changed view").check();
    await editor.getByLabel("Next action").fill("Check camera mount");
    await button(editor, "Save handoff").click();
    await expect(editor.getByText(/Next action · Check camera mount/)).toBeVisible();
    await editor.locator("summary").filter({ hasText: "Evidence bookmarks and retention" }).click();
    await editor.getByLabel("Bookmark name").fill("Camera tamper evidence");
    await button(editor, "Save evidence bookmark").click();
    await editor.getByLabel("Gateway management token").fill(token);
    await button(editor, "Preserve recorder clip for 7 days").click();
    await expect(editor.getByText(/Clip held until/)).toBeVisible();
    const downloading = page.waitForEvent("download");
    await button(editor, "Export evidence index").click();
    const file = await downloading;
    const evidence = JSON.parse(await readFile((await file.path())!, "utf8"));
    expect(evidence.clip.receipt.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(evidence.bookmarks[0].label).toBe("Camera tamper evidence");
    await editor.getByLabel("Apply a redaction rectangle to exported clips").check();
    await editor.getByLabel("Evidence export token").fill(token);
    const signed = page.waitForEvent("download");
    await button(editor, "Download signed evidence package").click();
    const zip = (await (await signed).path())!;
    const publicKey = join(state, "public-test.pem");
    execFileSync("openssl", ["pkey", "-in", join(state, "evidence-signing.pem"), "-pubout", "-out", publicKey]);
    const verifier = fileURLToPath(new URL("../../../services/camera-gateway/verify_evidence.py", import.meta.url));
    expect(execFileSync("python3", [verifier, zip, publicKey], { encoding: "utf8" })).toContain("Evidence verified");
    const manifest = JSON.parse(execFileSync("unzip", ["-p", zip, "manifest.json"], { encoding: "utf8" }));
    expect(manifest.clips[0].redactionPercent).toEqual([0, 80, 100, 20]);
    expect(manifest.clips[0].hold.sha256).toEqual(evidence.clip.receipt.sha256);

    await page.getByLabel("Observation JSON file").setInputFiles({
      name: "camera-health.json", mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({ type: "health", sensorId: cameraId, state: "online", occurredAt: new Date().toISOString() })),
    });
    await button(rail, "Posture").click();
    const videoHealth = page.getByRole("region", { name: "Camera video health" });
    await shutDown(publisher);
    await expect(videoHealth.getByRole("status")).toContainText("2 of 2 gateway cameras unavailable", { timeout: 25_000 });
    await expect(videoHealth.getByRole("region", { name: "Coverage affected by video outages" })).toContainText("North perimeter");
    publisher = publish();
    await waitForOutput(publisher, /Output #0, rtsp/);
    await expect(videoHealth.getByRole("status")).toContainText("0 of 2 gateway cameras unavailable", { timeout: 25_000 });
    await expect(button(videoHealth, "Video received · 2")).toBeVisible();
    await button(videoHealth, "Video received · 2").click();
    await videoHealth.getByRole("button", { name: /ONVIF loading dock.*Last video/ }).click();
    await editor.locator("summary", { hasText: "Video quality inspection" }).click();
    await editor.getByLabel("Observed issue").selectOption("obstructed");
    await button(editor, "Confirm video issue").click();
    await expect(editor.getByRole("status").filter({ hasText: "Confirmed obstructed view" })).toBeVisible();
    await button(editor, "Back to list").click();
    await expect(videoHealth.getByRole("region", { name: "Coverage affected by video quality" })).toContainText("North perimeter");
  } finally {
    await shutDown(gateway);
    await shutDown(publisher);
    await shutDown(gridPublisher);
    await shutDown(mt);
    onvif.close();
    await rm(data, { recursive: true, force: true });
  }
});

test("two Chrome operators receive persisted telemetry and transfer shared incident ownership", async ({ browser, baseURL }) => {
  test.setTimeout(60_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-shared-site-e2e-"));
  const alex = "a".repeat(64), sam = "b".repeat(64), reader = "c".repeat(64), adapter = "d".repeat(64);
  const digest = (token: string) => createHash("sha256").update(token).digest("hex");
  const roleFile = join(directory, "roles.json"), tokenFile = join(directory, "devices.json");
  await writeFile(roleFile, JSON.stringify({ site: "Loading dock pilot", deviceCameras: { "gate-01": "cam-02" }, operators: {
    alex: { name: "Alex", role: "operator", cameras: ["cam-02"], tokenSha256: digest(alex) },
    sam: { name: "Sam", role: "supervisor", cameras: "*", tokenSha256: digest(sam) },
  } }));
  await writeFile(tokenFile, JSON.stringify({ "gate-01": adapter }));
  const gateway = spawn("python3", [gatewayScript, "--port", "0", "--state-dir", join(directory, "state"), "--allow-source", "127.0.0.1/32",
    "--site-access", roleFile, "--telemetry-devices", tokenFile], { stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PROXIGO_TELEMETRY_READER_TOKEN: reader } });
  let first: Page | undefined, second: Page | undefined;
  try {
    const address = "http://127.0.0.1:" + (await waitForOutput(gateway, /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
    const request = () => fetch(address + "/api/site/telemetry", {
      method: "POST", headers: { Authorization: "Bearer " + adapter, "Content-Type": "application/json" }, body: JSON.stringify({
        id: "shared-door", sensorId: "gate-01", type: "observation", eventType: "door-forced-open", summary: "Shared site gate event", occurredAt: new Date().toISOString(),
      }),
    });
    expect((await (await request()).json()).accepted).toBe(true);
    expect((await (await request()).json()).accepted).toBe(false);

    async function connect(page: Page, token: string, name: string) {
      await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
      await page.goto(baseURL!);
      const rail = page.getByRole("navigation", { name: "Workspace panels" });
      await button(rail, "Posture").click();
      const posture = page.getByRole("region", { name: "Posture", exact: true });
      await posture.locator("summary", { hasText: "Shared site operator access" }).click();
      await posture.getByLabel("Shared site gateway URL").fill(address);
      await posture.getByLabel("Site operator token").fill(token);
      await button(posture, "Connect operator").click();
      await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
      await expect(posture.getByText(new RegExp(name + " · .*Loading dock pilot"))).toBeVisible();
      await posture.locator("summary", { hasText: "Site telemetry connection" }).click();
      await button(posture, "Use signed-in operator for telemetry").click();
      await expect(posture.getByText("Site telemetry is current.")).toBeVisible();
      await button(rail, "Alerts").click();
      await page.getByRole("button", { name: /^Shared site gate event/ }).click();
      return page.getByRole("complementary", { name: "Monitor and review" });
    }

    first = await browser.newPage(); second = await browser.newPage();
    const a = await connect(first, alex, "Alex");
    const b = await connect(second, sam, "Sam");
    await button(a, "Take ownership").click();
    await expect(b.getByText("Owner · Alex", { exact: true })).toBeVisible({ timeout: 8000 });
    await a.locator("summary", { hasText: "Response checklist and handoff" }).click();
    await a.getByLabel("Next action").fill("Confirm the hinge was repaired");
    await button(a, "Save handoff").click();
    await b.locator("summary", { hasText: "Response checklist and handoff" }).click();
    await button(b, "Accept handoff").click({ timeout: 8000 });
    await expect(a.getByText("Owner · Sam", { exact: true })).toBeVisible({ timeout: 8000 });
  } finally {
    await first?.close(); await second?.close(); await shutDown(gateway);
    await rm(directory, { recursive: true, force: true });
  }
});

test("published organization site alerts with both Chrome operators closed", async ({ browser, baseURL }) => {
  test.setTimeout(105_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-closed-browser-e2e-"));
  const adminToken = "8".repeat(64), doorToken = "6".repeat(64), cameraToken = "7".repeat(64), webhookSecret = "fixture-incident-webhook-secret";
  const keyPath = join(directory, "tls-key.pem"), certPath = join(directory, "tls-cert.pem");
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1", "-addext",
    "subjectAltName=IP:127.0.0.1", "-keyout", keyPath, "-out", certPath], { stdio: "ignore" });
  let receiverOnline = false, failedDeliveries = 0;
  let drillDeliveries = 0;
  const delivered: { payload: Record<string, string>; signatureValid: boolean }[] = [];
  const receiver = createHttpsServer({ key: await readFile(keyPath), cert: await readFile(certPath) }, async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (request.url === "/drill") {
      if (JSON.parse(body.toString()).type === "site-commissioning-test") drillDeliveries++;
      response.writeHead(204).end(); return;
    }
    if (!receiverOnline) { failedDeliveries++; response.writeHead(503).end(); return; }
    delivered.push({ payload: JSON.parse(body.toString()), signatureValid: request.headers["x-proxigo-signature"] === createHmac("sha256", webhookSecret).update(body).digest("hex") });
    response.writeHead(204).end();
  });
  const roles = join(directory, "organization.json"), tokens = join(directory, "devices.json");
  await writeFile(roles, JSON.stringify({ site: "East Campus", siteId: "east-campus", organization: { id: "harbor-logistics", name: "Harbor Logistics" }, operators: {
    jordan: { name: "Jordan", role: "org-admin", cameras: "*", tokenSha256: createHash("sha256").update(adminToken).digest("hex") },
  } }));
  await writeFile(tokens, JSON.stringify({ "gate-01": doorToken, "cam-02": cameraToken, "dock-switch": "5".repeat(64) }));
  let gateway: ChildProcess | undefined;
  let first: Page | undefined, second: Page | undefined;
  try {
    receiver.listen(0, "127.0.0.1"); await once(receiver, "listening");
    const receiverPort = (receiver.address() as { port: number }).port;
    async function start(port: string) {
      gateway = spawn("python3", [gatewayScript, "--port", port, "--state-dir", join(directory, "state"), "--allow-source", "127.0.0.1/32",
        "--site-access", roles, "--telemetry-devices", tokens, "--alert-webhook", `https://127.0.0.1:${receiverPort}/alert`,
        "--commissioning-webhook", `https://127.0.0.1:${receiverPort}/drill`], {
        stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, SSL_CERT_FILE: certPath, PROXIGO_WEBHOOK_SECRET: webhookSecret },
      });
      return (await waitForOutput(gateway, /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
    }
    const port = await start("0");
    const address = `http://127.0.0.1:${port}`;
    const rail = (page: Page) => page.getByRole("navigation", { name: "Workspace panels" });
    const posture = (page: Page) => page.getByRole("region", { name: "Posture", exact: true });
    const alertRegion = (page: Page) => page.getByRole("region", { name: "Alerts", exact: true });
    const panel = (page: Page) => page.getByRole("complementary", { name: "Monitor and review" });
    const config = (page: Page) => posture(page).locator("details").filter({ has: page.locator("summary", { hasText: /^Site configuration and versions$/ }) }).first();
    const configuration = async (page: Page) => {
      if (await config(page).getAttribute("open") === null) await config(page).locator(":scope > summary").click();
      return config(page);
    };
    async function connect(token: string) {
      const page = await browser.newPage();
      await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
      await page.goto(baseURL!);
      await button(rail(page), "Posture").click();
      await posture(page).locator("summary", { hasText: "Shared site operator access" }).click();
      await posture(page).getByLabel("Shared site gateway URL").fill(address);
      await posture(page).getByLabel("Site operator token").fill(token);
      await button(posture(page), "Connect operator").click();
      await expect(posture(page).getByText("Shared incident activity is current.")).toBeVisible();
      return page;
    }
    first = await connect(adminToken);
    const accounts = posture(first).locator("details").filter({ has: first.locator("summary", { hasText: /^Organization accounts$/ }) }).first();
    await accounts.locator(":scope > summary").click();
    await accounts.getByLabel("Account subject").fill("alex");
    await accounts.getByLabel("Operator display name").fill("Alex");
    await accounts.getByLabel("Camera scope").fill("cam-02");
    await button(accounts, "Create organization account").click();
    await button(accounts, "Generate site token").click();
    const alexToken = await accounts.getByLabel("New site token (shown once)").inputValue();
    expect(alexToken).toMatch(/^[0-9a-f]{64}$/);
    const adminAuth = { Authorization: "Bearer " + adminToken, "Content-Type": "application/json" };
    const operatorAuth = { Authorization: "Bearer " + alexToken, "Content-Type": "application/json" };
    expect((await fetch(address + "/api/site/accounts", { headers: operatorAuth })).status).toBe(403);
    expect((await fetch(address + "/api/site/configuration", { headers: operatorAuth })).status).toBe(403);

    await button(await configuration(first), "Start draft from preview inventory").click();
    await posture(first).locator("summary", { hasText: "Infrastructure and site readiness" }).click();
    await posture(first).getByLabel("Dependency ID").fill("dock-switch");
    await posture(first).getByLabel("Dependency name").fill("Dock PoE switch");
    await button(posture(first), "Save dependency to draft").click();
    for (const name of ["East gate contact", "Loading dock camera"]) {
      await button(rail(first), "Devices").click();
      await first.getByRole("region", { name: "Devices", exact: true }).getByRole("button", { name: new RegExp(`^${name}`) }).click();
      await button(panel(first), "Edit device").click();
      await panel(first).getByLabel("Dock PoE switch", { exact: true }).check();
      if (name === "Loading dock camera") {
        await panel(first).getByLabel("Horizontal FOV (°)").fill("360");
        await panel(first).getByLabel("Range (m)").fill("1200");
      }
      await button(panel(first), "Save device").click();
    }
    await button(rail(first), "Boundaries").click();
    await first.getByRole("button", { name: /^East access road/ }).click();
    await button(panel(first), "Edit boundary").click();
    await panel(first).getByLabel("Loading dock camera", { exact: true }).check();
    await button(panel(first), "Save boundary").click();
    await button(rail(first), "Alerts").click();
    await alertRegion(first).locator("summary", { hasText: "Door and camera correlation" }).click();
    await alertRegion(first).getByLabel("Group new matching events").check();
    await button(alertRegion(first), "Save correlation rule").click();
    await button(first, "Draft mode · Review changes").click();
    await button(await configuration(first), "Save site draft").click();
    await expect(posture(first).getByText("Shared site draft saved.")).toBeVisible();

    const makeEvents = (time: number) => [
      { id: "pilot-door", sensorId: "gate-01", eventType: "door-forced-open", occurredAt: new Date(time).toISOString(), summary: "Pilot door forced open" },
      { id: "pilot-camera", sensorId: "cam-02", eventType: "line-crossing", occurredAt: new Date(time + 125).toISOString(), summary: "Pilot camera line crossing", position: [-122.2974, 37.786] },
    ];
    const draft = await configuration(first);
    await draft.locator("summary", { hasText: "Run a configuration drill" }).click();
    await draft.getByLabel("Drill observations (JSON array)").fill(JSON.stringify(makeEvents(Date.now() - 1000)));
    await button(draft, "Preview draft with test events").click();
    await expect(draft.getByText(/2 boundary matches · 1 incident in the drill/)).toBeVisible();
    expect(failedDeliveries).toBe(0);
    await draft.getByLabel("Version note").fill("Door and camera pilot");
    await button(draft, "Publish saved site draft").click();
    await expect(first.getByText("Configuration version 1 published.")).toBeVisible();
    second = await connect(alexToken);
    await expect(second.getByText("Version 1", { exact: true })).toBeVisible();
    await expect(second.getByText("Harbor Logistics · East Campus")).toBeVisible();
    const outsider = await (await fetch(address + "/api/site/monitoring", { headers: operatorAuth })).json();
    expect(outsider.configuration.sensors.map((sensor: { id: string }) => sensor.id).sort()).toEqual(["cam-02", "gate-01"]);
    await expect(posture(first).getByText("Gateway configuration version 1 is live.")).toBeVisible();
    await posture(first).locator("summary", { hasText: "Infrastructure and site readiness" }).click();
    await posture(first).locator("summary", { hasText: "Plan a coverage outage" }).click();
    await posture(first).getByRole("combobox", { name: "Simulated offline device" }).selectOption("dependency:dock-switch");
    await expect(posture(first).getByText(/modeled coverage/).first()).toBeVisible();
    await first.close(); await second.close();

    async function postEvent(event: ReturnType<typeof makeEvents>[number], token: string) {
      const response = await fetch(address + "/api/site/telemetry", { method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        body: JSON.stringify({ ...event, type: "observation" }) });
      expect(response.status).toBe(200);
      return response.json();
    }
    const [door, camera] = makeEvents(Date.now());
    expect((await postEvent(door, doorToken)).accepted).toBe(true);
    await expect.poll(() => failedDeliveries).toBeGreaterThan(0);
    await shutDown(gateway);
    await start(port);
    expect((await postEvent(camera, cameraToken)).accepted).toBe(true);
    expect((await postEvent(camera, cameraToken)).accepted).toBe(false);
    receiverOnline = true;
    await expect.poll(() => delivered.length, { timeout: 8000 }).toBe(1);
    expect(delivered[0].signatureValid).toBe(true);
    expect(delivered[0].payload).toMatchObject({ type: "site-security-incident", siteId: "east-campus", organizationId: "harbor-logistics",
      configurationVersion: 1, eventType: "door-forced-open" });

    first = await connect(adminToken); second = await connect(alexToken);
    for (const page of [first, second]) {
      await button(rail(page), "Alerts").click();
      await expect(alertRegion(page).getByText("1 matching incident", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: /^Forced door · camera corroboration/ }).click();
      await expect(panel(page).getByText("Version 1", { exact: true })).toBeVisible();
      await expect(panel(page).getByRole("region", { name: "Correlated source events" })).toContainText("Pilot camera line crossing");
      await button(panel(page), "Replay timeline").click();
      await expect(panel(page).getByRole("list", { name: "Replay timeline" }).locator("strong")).toHaveText(["Pilot door forced open", "Pilot camera line crossing"]);
      await button(panel(page), "Back to incident").click();
    }
    await button(panel(second), "Take ownership").click();
    await expect(panel(first).getByText("Owner · Alex", { exact: true })).toBeVisible({ timeout: 8500 });
    expect(delivered).toHaveLength(1);
    await button(rail(second), "Devices").click();
    await second.getByRole("region", { name: "Devices", exact: true }).getByRole("button", { name: /^Loading dock camera/ }).click();
    await panel(second).locator("summary", { hasText: "Video quality inspection" }).click();
    await panel(second).getByRole("combobox", { name: "Observed issue" }).selectOption("dark");
    await button(panel(second), "Confirm video issue").click();
    await expect(panel(second).getByText(/Confirmed dark image/)).toBeVisible({ timeout: 8500 });
    const gap = await fetch(address + "/api/site/telemetry", { method: "POST", headers: { Authorization: "Bearer " + "5".repeat(64), "Content-Type": "application/json" },
      body: JSON.stringify({ type: "health", id: "dep-offline", sensorId: "dock-switch", state: "offline", occurredAt: new Date().toISOString() }) });
    expect(gap.status).toBe(200);
    await button(rail(first), "Posture").click();
    await expect(posture(first).getByRole("region", { name: "Video quality issues" })).toContainText("Confirmed · Dark image");
    const ready = posture(first).locator("details").filter({ has: first.locator("summary", { hasText: /^Infrastructure and site readiness$/ }) }).first();
    await ready.locator(":scope > summary").click();
    await ready.locator("summary", { hasText: "Dock PoE switch · Reported offline · 2 affected" }).click();
    await ready.getByLabel("Maintenance ticket URL").fill("https://support.example.test/tickets/108");
    await button(ready, "Link maintenance ticket").click();
    await expect(ready.getByRole("link", { name: "Open maintenance ticket" })).toHaveAttribute("href", "https://support.example.test/tickets/108");
    await ready.locator("summary", { hasText: "Commissioning and replacement" }).click();
    await ready.getByRole("combobox", { name: "Commissioning door" }).selectOption("gate-01");
    await ready.getByLabel("Observed the door event").check();
    await ready.getByLabel("Confirmed the linked incident and boundary").check();
    await button(ready, "Send drill notification").click();
    await expect(ready.getByText(/Delivery receipt/)).toBeVisible();
    await button(ready, "Save commissioning result").click();
    await expect(ready.getByText("East gate contact · Version 1 · Follow-up required")).toBeVisible();
    const insights = posture(first).getByRole("region", { name: "Site insights" });
    await button(insights, "Inspect East access road").click();
    const boundaryInsights = insights.getByRole("region", { name: "Readiness for East access road" });
    await expect(boundaryInsights).toContainText("Recording follow-up required");
    await expect(boundaryInsights).toContainText("Confirmed dark image");
    await expect(boundaryInsights).toContainText("Pilot door forced open");
    await button(rail(second), "Posture").click();
    const remoteInsights = posture(second).getByRole("region", { name: "Site insights" });
    await expect(remoteInsights).toContainText("Your assigned devices");
    await button(remoteInsights, "Inspect East access road").click();
    await expect(remoteInsights).toContainText("Recording follow-up required");
    await ready.getByLabel("Played the camera clip at the event time").check();
    await button(ready, "Save commissioning result").click();
    await expect(boundaryInsights).toContainText("Recording verified by drill");
    await expect(remoteInsights).toContainText("Recording verified by drill", { timeout: 22_000 });
    await expect(boundaryInsights).toContainText("Confirmed dark image");
    await first.route("**/api/site/monitoring?*", (route) => route.abort());
    await expect(insights).toContainText("Site disconnected. Showing the last available data.", { timeout: 12_000 });
    await expect(insights).toContainText("Last synced");
    await expect(boundaryInsights).toContainText("Recording verified by drill");
    expect(drillDeliveries).toBe(1);
    const history = await (await fetch(address + "/api/site/configuration", { headers: adminAuth })).json();
    expect(history.history[0].message).toBe("Door and camera pilot");
  } finally {
    await first?.close(); await second?.close(); await shutDown(gateway); receiver.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("organization dispatch opens the incident from the authenticated remote site", async ({ page, baseURL }) => {
  test.setTimeout(55_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-dispatch-e2e-"));
  const token = "2".repeat(64), adapter = "3".repeat(64);
  const processes: ChildProcess[] = [];

  async function start(id: string, name: string, organization = "pilot-organization") {
    const roles = join(directory, `${id}-access.json`), devices = join(directory, `${id}-device.json`);
    await writeFile(roles, JSON.stringify({ site: name, siteId: id, timeZone: "UTC", organization: { id: organization, name: "Dispatch organization" },
      operators: { dispatcher: { name: "Dispatcher", cameras: "*", role: "org-admin", tokenSha256: createHash("sha256").update(token).digest("hex") } } }));
    await writeFile(devices, JSON.stringify({ door: adapter }));
    const gateway = spawn("python3", [gatewayScript, "--port", "0", "--allow-source", "127.0.0.1/32", "--site-access", roles,
      "--telemetry-devices", devices, "--state-dir", join(directory, id)], { stdio: ["ignore", "pipe", "pipe"] });
    processes.push(gateway);
    const base = "http://127.0.0.1:" + (await waitForOutput(gateway, /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
    if (organization !== "pilot-organization") return base;
    const update = (payload: unknown) => fetch(base + "/api/site/configuration", { method: "POST", body: JSON.stringify(payload),
      headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" } });
    const saved = await update({ operation: "save", draftRevision: 0, publishedVersion: 0, configuration: {
      sensors: [{ id: "door", modality: "access", name: `${name} door`, position: [-122.3, 37.78] }], boundaries: [{ id: "site-yard", name: `${name} yard`,
        kind: "Restricted", enabled: true, responseTeam: `${name} response`, sensorIds: ["door"], vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] }],
    } });
    expect(saved.status).toBe(200);
    const published = await update({ operation: "publish", draftRevision: (await saved.json()).draftRevision, publishedVersion: 0, message: "Dispatch configuration" });
    expect(published.status).toBe(200);
    const event = await fetch(base + "/api/site/telemetry", { method: "POST", headers: { Authorization: "Bearer " + adapter, "Content-Type": "application/json" },
      body: JSON.stringify({ type: "observation", sensorId: "door", id: "door-open", occurredAt: new Date().toISOString(), eventType: "door-forced-open", summary: `${name} door forced open` }) });
    expect(event.status).toBe(200);
    return base;
  }

  try {
    const east = await start("east-site", "East facility");
    const west = await start("west-site", "West facility");
    const foreign = await start("foreign-site", "Foreign facility", "other-organization");
    await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
    await page.goto(baseURL!);
    const rail = page.getByRole("navigation", { name: "Workspace panels" });
    await button(rail, "Posture").click();
    const posture = page.getByRole("region", { name: "Posture", exact: true });
    await posture.locator("summary", { hasText: "Shared site operator access" }).click();
    await posture.getByLabel("Shared site gateway URL").fill(east);
    await posture.getByLabel("Site operator token", { exact: true }).fill(token);
    await button(posture, "Connect operator").click();
    await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
    const dispatch = posture.locator("details").filter({ has: page.locator("summary", { hasText: /^Organization dispatch$/ }) }).first();
    await dispatch.locator(":scope > summary").click();
    async function attach(url: string) {
      await dispatch.getByLabel("Additional site gateway URL").fill(url);
      await dispatch.getByLabel("Additional site operator token").fill(token);
      await button(dispatch, "Connect site to dispatch").click();
    }
    await attach(foreign);
    await expect(dispatch.getByRole("alert")).toContainText("different organization");
    await attach(west);
    await dispatch.getByRole("combobox", { name: "Dispatch site" }).selectOption("west-site");
    await expect(dispatch.getByRole("button", { name: /^East facility door forced open/ })).toHaveCount(0);
    await dispatch.getByRole("button", { name: /^West facility door forced open/ }).click();
    const inspector = page.getByRole("complementary", { name: "Monitor and review" });
    await expect(inspector.getByRole("heading", { name: "West facility door forced open" })).toBeVisible();
    await expect(inspector.getByText("West facility yard", { exact: true })).toBeVisible();
    await expect(inspector.getByText("West facility response", { exact: true })).toBeVisible();
    await expect(page.getByText("Dispatch organization · West facility")).toBeVisible();
  } finally {
    await Promise.all(processes.map(shutDown));
    await rm(directory, { recursive: true, force: true });
  }
});
