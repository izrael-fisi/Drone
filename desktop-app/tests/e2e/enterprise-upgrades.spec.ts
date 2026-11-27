import { expect, test, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer as tlsServer } from "node:https";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const source = (name: string) => fileURLToPath(new URL("../../../services/camera-gateway/" + name, import.meta.url));
const clipFixture = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const adminToken = "a".repeat(64), operatorToken = "b".repeat(64), adapter = "c".repeat(64), restoreToken = "d".repeat(64);
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
const position = [-122.30, 37.78];
const camera = { id: "cam-a", modality: "vision", name: "Gate camera", position };
const otherCamera = { ...camera, id: "cam-b", name: "Dock camera" };
const door = { id: "door", modality: "access", name: "Main gate", position, linkedCameraId: camera.id };
const boundary = { id: "gate-area", name: "Gate area", kind: "Restricted", enabled: true,
  sensorIds: [camera.id, otherCamera.id, door.id], vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] };
const procedure = { name: "Forced-door procedure", trigger: "door-forced-open", dueSeconds: 30, steps: [
  { id: "review", kind: "review", text: "Review camera evidence", next: "answer" },
  { id: "answer", kind: "question", text: "Does the incident require follow-up?", yes: "contact", no: "record" },
  { id: "contact", kind: "contact", text: "Call the shift supervisor", next: "record" },
  { id: "record", kind: "outcome", text: "Record the result" },
] };
const roster = { site: "Entrance site", siteId: "entrance", organization: { id: "operations", name: "Operations" },
  operators: { supervisor: { name: "Supervisor", role: "org-admin", cameras: "*", tokenSha256: hash(adminToken) },
    operator: { name: "First operator", role: "operator", cameras: [camera.id, otherCamera.id], tokenSha256: hash(operatorToken) } } };

async function stop(child?: ChildProcess) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const done = once(child, "exit");
  child.kill();
  const timer = setTimeout(() => child.kill("SIGKILL"), 4500);
  try { await done; } finally { clearTimeout(timer); }
}

async function start(state: string, roles: string, extras: string[] = [], port = "0") {
  const child = spawn("python3", [source("gateway.py"), "--port", port, "--state-dir", state, "--site-access", roles,
    "--allow-source", "127.0.0.1/32", ...extras], { stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  try {
    const address = await new Promise<string>((resolve, reject) => {
      const timeout = setTimeout(() => done(new Error(output || "Gateway startup timed out")), 8000);
      const read = (data: Buffer) => { output += data; const match = output.match(/Camera gateway listening on 127\.0\.0\.1:(\d+)/);
        if (match) done(undefined, "http://127.0.0.1:" + match[1]); };
      const exit = () => done(new Error(output || "Fixture exited"));
      function done(issue?: Error, address?: string) {
        clearTimeout(timeout); child.stdout!.off("data", read); child.stderr!.off("data", read); child.off("exit", exit);
        if (issue) reject(issue); else resolve(address!);
      }
      child.stdout!.on("data", read); child.stderr!.on("data", read); child.once("exit", exit);
    });
    return { child, address, state };
  } catch (issue) { await stop(child); throw issue; }
}

function fetchSite(address: string, route: string, body?: unknown, token = adminToken) {
  return fetch(address + "/api/site/" + route, { headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    ...(body !== undefined ? { method: "POST", body: JSON.stringify(body) } : {}) });
}

async function api(address: string, route: string, body?: unknown, token = adminToken) {
  const response = await fetchSite(address, route, body, token);
  const payload = await response.json();
  expect(response.status, JSON.stringify(payload)).toBe(200);
  return payload;
}

async function config(address: string, operation: string, fields: Record<string, unknown>) {
  const { publishedVersion, draftRevision } = await api(address, "configuration");
  return api(address, "configuration", { operation, publishedVersion, draftRevision, ...fields });
}

async function enter(page: Page, base: string, address: string, credential: string) {
  await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
  await page.goto(base);
  const nav = page.getByRole("navigation", { name: "Workspace panels" });
  await nav.getByRole("button", { name: "Posture", exact: true }).click();
  const posture = page.getByRole("region", { name: "Posture", exact: true });
  await posture.getByText("Shared site operator access", { exact: true }).click();
  await posture.getByLabel("Shared site gateway URL").fill(address);
  await posture.getByLabel("Site operator token", { exact: true }).fill(credential);
  await posture.getByRole("button", { name: "Connect operator" }).click();
  await expect(posture.getByText("Shared incident activity is current.")).toBeVisible();
  return posture;
}

const summary = (page: Page, name: string) => page.locator("summary").filter({ hasText: new RegExp("^" + name + "$") });
const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name, exact: true });
const inspector = (page: Page) => page.getByRole("complementary", { name: "Monitor and review" });

async function openDoor(page: Page) {
  await nav(page, "Alerts").click();
  await page.getByRole("button", { name: /^Forced door at the fixture/ }).click();
  await expect(inspector(page).getByRole("heading", { name: "Forced door at the fixture" })).toBeVisible();
  return inspector(page);
}

function makeCertificate(directory: string) {
  const cert = join(directory, "cert.pem"), key = join(directory, "key.pem");
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-addext",
    "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", cert, "-days", "1"], { stdio: "pipe" });
  return { cert, key };
}

test("two Chrome operators complete a pinned procedure and export a two-camera case through restart and restore", async ({ browser, baseURL }) => {
  test.setTimeout(135_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-enterprise-response-")), rules = join(directory, "roles.json"), devices = join(directory, "devices.json");
  const state = join(directory, "state"), cameraKeys = { "cam-a": "1".repeat(48), "cam-b": "2".repeat(48) };
  const { cert, key } = makeCertificate(directory);
  let gateway: Awaited<ReturnType<typeof start>> | undefined;
  const first = await browser.newPage(), second = await browser.newPage();
  try {
    await writeFile(rules, JSON.stringify(roster));
    await writeFile(devices, JSON.stringify({ door: adapter, "cam-b": adapter }));
    await mkdir(join(state, "clips"), { recursive: true });
    await writeFile(join(state, "cameras.json"), JSON.stringify(Object.fromEntries(Object.entries(cameraKeys).map(([id, cameraKey]) =>
      [id, { key: cameraKey, url: "rtsp://127.0.0.1/fixtures" }]))));
    const extras = ["--telemetry-devices", devices, "--axis-recorder", "http://127.0.0.1:47999", "--require-camera-session"];
    gateway = await start(state, rules, extras);
    let { address } = gateway;
    const published = { sensors: [camera, otherCamera, door], boundaries: [boundary], procedures: [procedure] };
    await config(address, "save", { configuration: published });
    await config(address, "publish", { message: "Fixture forced-door response" });
    await new Promise((resolve) => setTimeout(resolve, 50));
    const stamp = Date.now();
    for (const [id, value] of Object.entries(cameraKeys)) {
      const at = stamp + (id === "cam-a" ? 0 : 1);
      const clipId = hash(value + ":" + at).slice(0, 32);
      await copyFile(clipFixture, join(state, "clips", clipId + ".mp4"));
      await writeFile(join(state, "clips", clipId + ".json"), JSON.stringify({ cameraKey: value, clipId, occurredAt: at,
        eventOffsetSeconds: 0.2, durationSeconds: 1, expiresAt: Math.floor(Date.now() / 1000) + 600 }));
    }
    for (const item of [
      { id: "force-1", sensorId: "door", eventType: "door-forced-open", occurredAt: new Date(stamp).toISOString(), summary: "Forced door at the fixture" },
      { id: "second-camera", sensorId: "cam-b", eventType: "motion", occurredAt: new Date(stamp + 1).toISOString(), summary: "Dock camera corroboration" },
    ]) await api(address, "telemetry", { ...item, type: "observation" }, adapter);
    await enter(first, baseURL!, address, operatorToken);
    let pane = await openDoor(first);
    await pane.getByRole("button", { name: "Take ownership" }).click();
    await pane.getByRole("button", { name: "Evidence reviewed" }).click();
    await pane.getByRole("button", { name: "No", exact: true }).click();
    await expect(pane.getByText("Record the result", { exact: true }).first()).toBeVisible();
    await summary(first, "Response checklist and handoff").click();
    await pane.getByLabel("Next action").fill("Document fixture results");
    await pane.getByRole("button", { name: /handoff/i }).last().click();
    await expect(pane.getByText(/Next action · Document fixture results/)).toBeVisible();

    await config(address, "save", { configuration: { ...published, procedures: [{ ...procedure, name: "Version two procedure" }] } });
    await config(address, "publish", { message: "New procedure name" });
    const port = new URL(address).port;
    await stop(gateway.child);
    gateway = await start(state, rules, extras, port);
    address = gateway.address;
    await enter(second, baseURL!, address, adminToken);
    pane = await openDoor(second);
    await expect(pane.getByRole("heading", { name: procedure.name })).toBeVisible();
    await summary(second, "Response checklist and handoff").click();
    await pane.getByRole("button", { name: "Accept handoff" }).click();
    await pane.getByLabel("Procedure note").fill("No additional response needed after reviewing both cameras.");
    await pane.getByRole("button", { name: "Record finding" }).click();
    await expect(pane.getByText("Response procedure complete.")).toBeVisible();
    await pane.getByLabel("Disposition", { exact: true }).selectOption("inconclusive");
    await pane.getByRole("button", { name: "Resolve alert" }).click();
    await expect(pane.getByText("Resolved", { exact: true })).toBeVisible();

    await pane.getByRole("button", { name: "Open incident case workspace" }).click();
    const evidence = second.getByRole("dialog", { name: "Evidence search and cases" });
    await evidence.getByRole("button", { name: "Create case", exact: true }).click();
    await evidence.getByLabel("Evidence keywords").fill("Forced door");
    await evidence.getByRole("button", { name: "Search evidence", exact: true }).click();
    await expect(evidence.getByRole("button", { name: "Attach to open case" }).first()).toBeVisible();
    await evidence.getByRole("button", { name: "Attach to open case" }).first().click();
    await expect(evidence.getByRole("region", { name: "Open case evidence" }).getByRole("button", { name: "Retrieve case recording" })).toHaveCount(1);
    await evidence.getByLabel("Evidence keywords").fill("Dock corroboration");
    await evidence.getByRole("button", { name: "Search evidence", exact: true }).click();
    await expect(evidence.getByText("Dock camera corroboration", { exact: true }).first()).toBeVisible();
    await evidence.getByRole("button", { name: "Attach to open case" }).first().click();

    const records = evidence.getByRole("region", { name: "Open case evidence" });
    await expect(records.getByRole("button", { name: "Retrieve case recording" })).toHaveCount(2);
    for (let index = 0; index < 2; index++) {
      await records.getByRole("button", { name: "Retrieve case recording" }).nth(index).click();
      await expect(records.getByRole("button", { name: "Hold recording" }).nth(index)).toBeEnabled({ timeout: 20000 });
      await records.getByRole("button", { name: "Hold recording" }).nth(index).click();
      await expect(records.getByLabel("Include in signed export").nth(index)).toBeChecked();
    }
    await records.getByRole("button", { name: "Play case recording" }).first().click();
    await expect.poll(() => evidence.locator("video").last().evaluate((node) => (node as HTMLVideoElement).readyState)).toBeGreaterThan(1);
    const waitDownload = second.waitForEvent("download");
    await records.getByRole("button", { name: "Export signed case" }).click();
    const file = join(directory, "case.zip"), publicKey = join(directory, "evidence-public.pem");
    await (await waitDownload).saveAs(file);
    execFileSync("openssl", ["pkey", "-in", join(state, "evidence-signing.pem"), "-pubout", "-out", publicKey]);
    expect(execFileSync("python3", [source("verify_evidence.py"), file, publicKey]).toString()).toContain("Evidence verified");

    const caseId = (await api(address, "cases")).cases[0].id;
    const before = await api(address, "cases?" + new URLSearchParams({ id: caseId }));
    const approved = join(directory, "approved.json"), archive = join(directory, "backup.p7m"), restored = join(directory, "restored");
    await writeFile(approved, JSON.stringify({ ...roster, operators: { supervisor: { ...roster.operators.supervisor, tokenSha256: hash(restoreToken) } } }));
    await stop(gateway.child);
    execFileSync("python3", [source("gateway_backup.py"), "backup", "--state-dir", state, "--archive", archive, "--recipient-cert", cert]);
    const receipt = JSON.parse(execFileSync("python3", [source("gateway_backup.py"), "restore", "--state-dir", restored, "--archive", archive,
      "--recipient-cert", cert, "--recipient-key", key, "--approved-access", approved]).toString());
    await test.info().attach("Encrypted gateway restore receipt", { body: JSON.stringify(receipt), contentType: "application/json" });
    gateway = await start(restored, approved, extras, port);
    expect((await fetchSite(address, "cases", undefined, operatorToken)).status).toBe(401);
    const after = await api(address, "cases?" + new URLSearchParams({ id: caseId }), undefined, restoreToken);
    expect(after.case).toEqual(before.case);
    const reopened = await browser.newPage();
    try {
      const posture = await enter(reopened, baseURL!, address, restoreToken);
      await summary(reopened, "Site operations and connectors").click();
      await expect(posture.getByText("Last verified backup snapshot")).toBeVisible();
      await expect(posture.getByText("Last verified backup snapshot").locator("..")).not.toContainText("No record");
    } finally { await reopened.close(); }
  } finally {
    await first.close(); await second.close(); await stop(gateway?.child);
    await rm(directory, { recursive: true, force: true });
  }
});

test("bulk import previews 12 cameras with doors and boundaries, then publishes and restores", async ({ page, baseURL }) => {
  const directory = await mkdtemp(join(tmpdir(), "proxigo-enterprise-inventory-")), roles = join(directory, "roles.json");
  await writeFile(roles, JSON.stringify(roster));
  const gateway = await start(join(directory, "state"), roles);
  try {
    const { address } = gateway;
    await config(address, "save", { configuration: { sensors: [], boundaries: [], floors: [
      { id: "warehouse", name: "Warehouse level", building: "Site building", imageUrl: "https://example.test/floor.png",
        corners: boundary.vertices },
    ] } });
    await config(address, "publish", { message: "Floor plan" });
    await enter(page, baseURL!, address, adminToken);
    await summary(page, "Site configuration and versions").click();
    await summary(page, "Import site inventory").click();
    const header = "id,name,type,longitude,latitude,floorId,boundaryIds,linkedCameraId\n";
    const data = header + Array.from({ length: 12 }, (_, index) =>
      `camera-${index + 1},Camera ${index + 1},camera,-122.3,37.78,${index > 5 ? "warehouse,indoor" : ",outdoor"},`).join("\n") +
      "\ndoor-1,Gate door,door,-122.3,37.78,,outdoor,camera-1\ndoor-2,Warehouse door,door,-122.3,37.78,warehouse,indoor,camera-7\n";
    const geo = { type: "FeatureCollection", features: ["outdoor", "indoor"].map((id) => ({
      type: "Feature", id, properties: { name: id === "indoor" ? "Indoor perimeter" : "Outdoor perimeter", kind: "Perimeter", floorId: id === "indoor" ? "warehouse" : null },
      geometry: { type: "Polygon", coordinates: [[...boundary.vertices, boundary.vertices[0]]] },
    })) };
    const csv = (content: string) => page.getByLabel("Device CSV").setInputFiles({ name: "devices.csv", mimeType: "text/csv", buffer: Buffer.from(content) });
    await page.getByLabel("Boundary GeoJSON").setInputFiles({ name: "boundaries.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(geo)) });
    await csv(data + "invalid,Bad door,door,230,37.78,,outdoor,\n");
    await page.getByRole("button", { name: "Preview inventory import" }).click();
    await expect(page.getByRole("region", { name: "Inventory import review" })).toContainText("1 errors");
    expect((await api(address, "configuration")).draft).toBeNull();
    await csv(data);
    await page.getByRole("button", { name: "Preview inventory import" }).click();
    await expect(page.getByRole("region", { name: "Inventory import review" })).toContainText("16 changes");
    await page.getByRole("button", { name: "Preview inventory on map" }).click();
    await page.getByRole("button", { name: "Return to inventory import" }).click();
    await page.getByRole("button", { name: "Apply inventory to draft" }).click();
    await expect(page.getByText("Inventory saved to the shared draft.", { exact: false })).toBeVisible();
    await page.getByLabel("Version note").fill("Inventory fixture");
    await page.getByRole("button", { name: "Publish saved site draft", exact: true }).click();
    await expect.poll(async () => (await api(address, "configuration")).published.sensors.length).toBe(14);
    expect((await api(address, "configuration")).published.boundaries.every(({ enabled }: { enabled: boolean }) => !enabled)).toBe(true);
    await page.getByRole("button", { name: "Preview inventory import" }).click();
    await expect(page.getByRole("region", { name: "Inventory import review" })).toContainText("14 unchanged");
    const rollback = await config(address, "restore", { restoreVersion: 1, message: "Restore original floor plan" });
    expect(rollback.published.sensors).toEqual([]);
  } finally { await stop(gateway.child); await rm(directory, { recursive: true, force: true }); }
});

test("XProtect and Genetec HTTPS fixtures import inventories, deduplicate events, and preserve recorder context", async ({ browser, baseURL }) => {
  test.setTimeout(85000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-enterprise-vms-"));
  const { cert, key } = makeCertificate(directory);
  const roles = join(directory, "roles.json"), xpToken = join(directory, "xprotect-token");
  const xpMap = join(directory, "xprotect-map.json"), webCredentials = join(directory, "genetec-credentials.json"), webMap = join(directory, "genetec-map.json");
  const manager = "11111111-1111-4111-8111-111111111111", xpCamera = "22222222-2222-4222-8222-222222222222";
  const xpType = "33333333-3333-4333-8333-333333333333", xpEvent = "44444444-4444-4444-8444-444444444444";
  const genDoor = "55555555-5555-4555-8555-555555555555", genCamera = "66666666-6666-4666-8666-666666666666";
  const mediaSession = "77777777-7777-4777-8777-777777777777";
  const state = join(directory, "state");
  const operator = await browser.newPage(), peer = await browser.newPage();
  let gateway: Awaited<ReturnType<typeof start>> | undefined, eventTime = "", requestedTime = "";
  let reportRequests = 0, subscriptions = 0, xprotectRequests = 0, allowEvents = false;
  const fixtureErrors: string[] = [];
  const vendor = tlsServer({ cert: await readFile(cert), key: await readFile(key) });
  vendor.on("request", async (request, response) => {
    try {
      const url = new URL(request.url!, "https://127.0.0.1"), q = url.searchParams.get("q") ?? "";
      const json = (value: unknown) => { response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(value)); };
      const sdk = (value: unknown) => json({ Rsp: { Status: "Ok", Result: value } });
      if (url.pathname.startsWith("/WebSdk/")) {
        if (request.headers.authorization !== "Basic " + Buffer.from("sdk;fixture:password").toString("base64")) return response.writeHead(401).end();
        if (url.pathname.endsWith("EntityConfiguration")) {
          sdk(q.includes("EntityTypes@Role") ? [{ Guid: manager, Name: "Fixture Access Manager", Type: "AccessManager" }]
            : q.includes("EntityTypes@Camera") ? [{ Guid: genCamera, Name: "Genetec warehouse camera" }] : [{ Guid: genDoor, Name: "Genetec warehouse door" }]);
        } else if (url.pathname.endsWith("AccessControlRawEvent")) {
          reportRequests++;
          const cursor = Number(q.match(/RawEventIndex\([\w-]+,(\d+)\)/)?.[1] ?? 0);
          sdk(allowEvents && cursor < 100 ? [{ Position: 100, AccessManagerGuid: manager, SourceGuid: genDoor,
            EventType: 32, EventTimestamp: eventTime.replace(/Z$/, "") }] : []);
        } else if (url.pathname.endsWith("/subscribe")) { subscriptions++; sdk([]); }
        else if (url.pathname.endsWith("/unsubscribe")) sdk([]);
        else if (url.pathname.endsWith("/events")) {
          response.writeHead(200, { "Content-Type": "multipart/mixed; boundary=GENETECBOUNDARY" });
          response.write("--GENETECBOUNDARY\r\nContent-Type: text/json\r\n\r\n" + JSON.stringify({ Rsp: { Status: "Ok", Result: { EventType: "DoorOpenWhileLockSecure" } } }) + "\r\n");
          response.end("--GENETECBOUNDARY--\r\n");
        } else response.writeHead(404).end();
        return;
      }
      if (request.headers.authorization !== "Bearer fixture-xprotect-token") return response.writeHead(401).end();
      if (url.pathname.endsWith("/cameras")) json([{ id: xpCamera, name: "XProtect gate camera" }]);
      else if (url.pathname.endsWith("/events")) {
        xprotectRequests++;
        const after = Number(url.searchParams.get("localId")?.split(":")[1] ?? 0);
        json(allowEvents && after < 10 ? [{ id: xpEvent, localId: 10, time: eventTime, type: xpType, source: "doors/fixture" }] : []);
      } else if (url.pathname.endsWith("/webRTC/session") || url.pathname.endsWith("/webRTC/session/" + mediaSession)) {
        const parts = [];
        for await (const chunk of request) parts.push(chunk);
        const body = JSON.parse(Buffer.concat(parts).toString());
        if (request.method === "POST") {
          requestedTime = body.playbackTimeNode.playbackTime;
          expect(body.cameraId).toBe(xpCamera);
          const offerSDP = await peer.evaluate(async () => {
            const windowState = window as Window & { fixturePeer?: RTCPeerConnection };
            windowState.fixturePeer?.close();
            const connection = windowState.fixturePeer = new RTCPeerConnection();
            const canvas = document.createElement("canvas"); canvas.width = 160; canvas.height = 90;
            const context = canvas.getContext("2d")!;
            context.fillStyle = "orange"; context.fillRect(0, 0, 160, 90);
            const stream = canvas.captureStream(10);
            const timer = setInterval(() => { context.fillStyle = context.fillStyle === "#ffa500" ? "black" : "orange"; context.fillRect(0, 0, 160, 90); }, 100);
            connection.onconnectionstatechange = () => { if (connection.connectionState === "closed") clearInterval(timer); };
            connection.addTrack(stream.getVideoTracks()[0], stream);
            await connection.setLocalDescription(await connection.createOffer());
            await new Promise((resolve) => setTimeout(resolve, 750));
            return connection.localDescription!.sdp;
          });
          json({ sessionId: mediaSession, offerSDP, iceServers: [] });
        } else {
          await peer.evaluate(async (sdp) => {
            const connection = (window as Window & { fixturePeer?: RTCPeerConnection }).fixturePeer;
            await connection!.setRemoteDescription({ type: "answer", sdp });
          }, body.answerSDP);
          json({});
        }
      } else if (url.pathname.endsWith("/webRTC/iceCandidates/" + mediaSession)) json({ iceCandidates: [] });
      else response.writeHead(404).end();
    } catch (issue) {
      fixtureErrors.push(String(issue));
      response.writeHead(500).end();
    }
  });
  try {
    vendor.listen(0, "127.0.0.1"); await once(vendor, "listening");
    const origin = "https://127.0.0.1:" + (vendor.address() as { port: number }).port;
    await Promise.all([
      writeFile(roles, JSON.stringify(roster)), writeFile(xpToken, "fixture-xprotect-token"),
      writeFile(xpMap, JSON.stringify({ eventTypes: { [xpType]: "door-forced-open" }, sources: { "doors/fixture": "door" } })),
      writeFile(webCredentials, JSON.stringify({ username: "sdk", applicationId: "fixture", password: "password" })),
      writeFile(webMap, JSON.stringify({ [genDoor]: "vendor-door" })),
    ]);
    await peer.goto(baseURL!);
    const options = ["--milestone-origin", origin, "--milestone-token-file", xpToken, "--milestone-event-map", xpMap,
      "--genetec-origin", origin, "--genetec-credentials", webCredentials, "--genetec-device-map", webMap, "--vms-ca", cert];
    gateway = await start(state, roles, options);
    let { address } = gateway;
    const posture = await enter(operator, baseURL!, address, adminToken);
    await summary(operator, "Site configuration and versions").click();
    await operator.getByRole("button", { name: "Start draft from preview inventory" }).click();
    await summary(operator, "XProtect camera catalog").click();
    await operator.getByRole("button", { name: "Load XProtect channels" }).click();
    await expect(operator.getByLabel("XProtect channel")).toContainText("XProtect gate camera");
    await summary(operator, "Genetec device catalog").click();
    await operator.getByRole("button", { name: "Load Genetec channels" }).click();
    await expect(operator.getByLabel("Genetec channel")).toContainText("Genetec warehouse door");
    expect((await api(address, "vms-channels?provider=genetec")).channels).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: genDoor, deviceId: "vendor-door", modality: "access" }),
    ]));
    const xpSensor = { ...camera, id: "xprotect-cam", cameraChannel: xpCamera, cameraProvider: "Milestone XProtect" };
    const genSensor = { ...door, id: "vendor-door", name: "Genetec fixture door", linkedCameraId: otherCamera.id };
    const garden = { ...boundary, sensorIds: [xpSensor.id, "door", otherCamera.id, genSensor.id] };
    await config(address, "save", { configuration: { sensors: [xpSensor, { ...door, linkedCameraId: xpSensor.id }, genSensor, otherCamera],
      boundaries: [garden], procedures: [procedure] } });
    await config(address, "publish", { message: "Read-only vendor fixture" });
    await new Promise((resolve) => setTimeout(resolve, 50));
    eventTime = new Date().toISOString(); allowEvents = true;
    await expect.poll(async () => (await api(address, "dispatch")).alerts.length, { timeout: 25000 }).toBe(2);
    await operator.reload();
    await enter(operator, baseURL!, address, adminToken);
    await summary(operator, "Site operations and connectors").click();
    await expect(posture.getByRole("heading", { name: "Genetec Security Center" })).toBeVisible();
    await nav(operator, "Alerts").click();
    await operator.getByRole("button", { name: /^XProtect door forced open/ }).click();
    await expect(inspector(operator).getByRole("heading", { name: procedure.name })).toBeVisible();
    await inspector(operator).getByRole("button", { name: "Play from XProtect recorder" }).click();
    await expect.poll(async () => {
      if (fixtureErrors.length) throw new Error(fixtureErrors.join("\n"));
      return inspector(operator).getByLabel("XProtect recorded video").evaluate((node) => (node as HTMLVideoElement).readyState);
    }, { timeout: 20000 }).toBeGreaterThan(1);
    expect(Date.parse(requestedTime)).toBe(Date.parse(eventTime));
    expect(fixtureErrors).toEqual([]);

    const port = new URL(address).port;
    await stop(gateway.child);
    gateway = await start(state, roles, options, port);
    address = gateway.address;
    const previousReport = reportRequests;
    await expect.poll(() => reportRequests).toBeGreaterThan(previousReport);
    expect((await api(address, "dispatch")).alerts.length).toBe(2);
    expect(subscriptions).toBeGreaterThan(0);
    expect(xprotectRequests).toBeGreaterThan(1);
    const report = (await api(address, "dispatch")).alerts.map((event: { observation: { vendor: unknown } }) => event.observation.vendor);
    await test.info().attach("Vendor event provenance and playback clock", { contentType: "application/json",
      body: JSON.stringify({ report, requestedTime, subscriptions, reportRequests, xprotectRequests }) });
  } finally {
    await operator.close(); await peer.close(); await stop(gateway?.child);
    vendor.closeAllConnections(); vendor.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("critical coverage opens one incident, records recovery, and isolates an unreachable dispatch site", async ({ page, baseURL }) => {
  const media = process.env.MEDIAMTX;
  test.skip(!media, "Set MEDIAMTX to run the real RTSP coverage fixture.");
  test.setTimeout(155000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-enterprise-coverage-"));
  const state = join(directory, "state"), roles = join(directory, "roles.json"), otherRoles = join(directory, "remote.json");
  const { createServer } = await import("node:net");
  const reservation = createServer().listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const rtspPort = (reservation.address() as { port: number }).port;
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  const mediaConfig = join(directory, "mediamtx.yml");
  const address = "rtsp://127.0.0.1:" + rtspPort + "/critical-camera";
  const cameraKey = "3".repeat(48);
  let router: ChildProcess | undefined, publisher: ChildProcess | undefined;
  let first: Awaited<ReturnType<typeof start>> | undefined, second: Awaited<ReturnType<typeof start>> | undefined;
  const startPublisher = () => spawn("ffmpeg", ["-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1",
    "-i", clipFixture, "-an", "-c:v", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", address], { stdio: "ignore" });
  const operations = (site: string, token = adminToken) => api(site, "operations", undefined, token);
  try {
    await Promise.all([
      writeFile(roles, JSON.stringify(roster)),
      writeFile(otherRoles, JSON.stringify({ ...roster, site: "Remote yard", siteId: "remote-yard" })),
      writeFile(mediaConfig, `logLevel: warn
rtspAddress: 127.0.0.1:${rtspPort}
rtspTransports: [tcp]
rtmp: no
hls: no
webrtc: no
srt: no
paths:
  all_others:
`),
      mkdir(state, { recursive: true }),
    ]);
    router = spawn(media!, [mediaConfig], { stdio: "ignore" });
    await expect.poll(async () => {
      const { createConnection } = await import("node:net");
      return new Promise((resolve) => {
        const connection = createConnection(rtspPort, "127.0.0.1");
        connection.once("connect", () => { connection.destroy(); resolve(true); });
        connection.once("error", () => resolve(false));
      });
    }).toBe(true);
    publisher = startPublisher();
    await writeFile(join(state, "cameras.json"), JSON.stringify({ [camera.id]: { key: cameraKey, url: address } }));
    first = await start(state, roles, ["--probe-interval", "5"]);
    second = await start(join(directory, "remote"), otherRoles);
    const own = first.address;
    await config(own, "save", { configuration: { sensors: [{ ...camera,
      cameraGatewayKind: "rtsp", cameraFeedUrl: own + "/streams/" + cameraKey + "/index.m3u8",
      cameraView: { bearingDeg: 0, fovDeg: 90, rangeM: 80 } }], boundaries: [{ ...boundary, sensorIds: [camera.id] }],
      procedures: [{ ...procedure, name: "Coverage response", trigger: "coverage-loss", boundaryId: boundary.id }] } });
    await config(own, "publish", { message: "Coverage monitoring" });
    await api(own, "assets", { operation: "check-camera", mode: "verify", cameraId: camera.id, version: 1 });
    await expect.poll(async () => (await operations(own)).coverage[0]?.state, { timeout: 30000 }).toBe("available");
    await enter(page, baseURL!, own, adminToken);
    await summary(page, "Organization dispatch").click();
    const dispatch = page.locator("details").filter({ has: page.locator("summary", { hasText: /^Organization dispatch$/ }) }).first();
    await dispatch.getByLabel("Additional site gateway URL").fill(second.address);
    await dispatch.getByLabel("Additional site operator token").fill(adminToken);
    await dispatch.getByRole("button", { name: "Connect site to dispatch" }).click();
    await expect(dispatch.getByRole("button", { name: /Remote yard.*Gateway responding/ })).toBeVisible();
    await stop(second.child);
    await dispatch.getByRole("button", { name: "Refresh dispatch sites" }).click();
    await expect(dispatch.getByRole("button", { name: /Remote yard.*Gateway unreachable/ })).toBeDisabled();
    await expect(dispatch.getByRole("button", { name: /Entrance site.*Gateway responding/ })).toBeEnabled();
    await summary(page, "Site operations and connectors").click();
    await expect(page.getByText(/Verified camera video available/)).toBeVisible();
    const lostAt = Date.now();
    await stop(publisher);
    await expect.poll(async () => (await operations(own)).coverage[0]?.incidentId ?? "", { timeout: 90000 }).toMatch(/^coverage-/);
    const outageTime = Date.now() - lostAt;
    await page.getByRole("button", { name: "Refresh site operations" }).click();
    await page.getByRole("button", { name: "Open critical coverage incident" }).click();
    await expect(inspector(page).getByRole("heading", { name: "Coverage response" })).toBeVisible();
    await expect(inspector(page).getByText("Critical camera coverage lost · Gate area", { exact: true }).first()).toBeVisible();
    expect((await api(own, "dispatch")).alerts).toHaveLength(1);
    publisher = startPublisher();
    await expect.poll(async () => (await api(own, "dispatch")).alerts[0].coverageRecoveredAt, { timeout: 30000 }).toBeTruthy();
    await expect(inspector(page).getByText("Coverage restored", { exact: true })).toBeVisible();
    expect((await api(own, "dispatch")).alerts).toHaveLength(1);
    await test.info().attach("Coverage incident and recording recovery", { contentType: "application/json", body: JSON.stringify({
      outageTimeMs: outageTime, alert: (await api(own, "dispatch")).alerts[0], recovered: (await operations(own)).coverage[0] }) });
  } finally {
    await stop(publisher); await stop(first?.child); await stop(second?.child); await stop(router);
    await rm(directory, { recursive: true, force: true });
  }
});

test("site intelligence keeps handoff drafts private, reviews recorded frames, and corroborates the gate", async ({ browser, baseURL }, testInfo) => {
  test.setTimeout(100_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-site-intelligence-"));
  const state = join(directory, "state"), roles = join(directory, "roles.json"), adapters = join(directory, "adapters.json");
  const settings = join(directory, "intelligence.json"), cameraKey = "1".repeat(48);
  const first = await browser.newPage(), second = await browser.newPage();
  const { localInferenceFixture } = await import("./fixtures/local-inference");
  const provider = await localInferenceFixture();
  const radar = await gateRadarFixture();
  let gateway: Awaited<ReturnType<typeof start>> | undefined;
  try {
    const radarDevice = { id: "radar", modality: "rf", name: "Gate radar", position };
    await writeFile(roles, JSON.stringify(roster));
    await writeFile(adapters, JSON.stringify({ door: adapter }));
    await mkdir(join(state, "clips"), { recursive: true });
    await writeFile(join(state, "cameras.json"), JSON.stringify({ [camera.id]: { key: cameraKey, url: "rtsp://127.0.0.1/fixture" } }));
    await writeFile(settings, JSON.stringify({
      deviceDegradation: true,
      ai: { provider: "on-premises", localOrigin: provider.origin, handoff: true, clipReview: true, clipBoundaries: [boundary.id],
        masks: { [camera.id]: [[0, 0, 50, 50]] } },
      gateRadars: [{ sensorId: radarDevice.id, cameraId: camera.id, doorId: door.id, boundaryId: boundary.id,
        axisProfile: "Channel0Profile1", doorClockVerified: true, address: radar.endpoint }],
    }));
    gateway = await start(state, roles, ["--site-intelligence", settings, "--axis-recorder", "http://127.0.0.1:47999",
      "--telemetry-devices", adapters, "--require-camera-session"]);
    const { address } = gateway;
    const localCamera = { ...camera, cameraGatewayKind: "rtsp", cameraFeedUrl: address + "/streams/" + cameraKey + "/index.m3u8" };
    await config(address, "save", { configuration: { sensors: [localCamera, otherCamera, door, radarDevice], procedures: [procedure],
      boundaries: [{ ...boundary, sensorIds: [...boundary.sensorIds, radarDevice.id] }] } });
    await config(address, "publish", { message: "Intelligence fixture" });
    execFileSync("python3", ["-c", `
from hashlib import sha256
import json, sqlite3, sys, time
from datetime import timezone
sys.path.insert(0, sys.argv[3])
from device_degradation import BUCKET, DAY, DeviceDegradation, regime
with sqlite3.connect(sys.argv[1]) as db:
    body = db.execute("SELECT body FROM configuration_versions ORDER BY version DESC LIMIT 1").fetchone()[0]
    camera = next(row for row in json.loads(body)["sensors"] if row["id"] == "cam-a")
    identity = {name: camera.get(name) for name in ("position", "cameraView", "analyticsPlacement", "analyticsRules")}
    key = sha256(json.dumps([sys.argv[2], identity], sort_keys=True).encode()).hexdigest()
    start = int(time.time() // BUCKET) * BUCKET - 3 * BUCKET
    for index in range(3):
        at = start + index * BUCKET
        period = regime(timezone.utc, at)
        for day in range(1, 8):
            for sample in range(8):
                db.execute("INSERT OR IGNORE INTO camera_drift_buckets VALUES('cam-a',?,'frame-age',?,?,1,2,1)",
                           (key, at - day * DAY - sample * BUCKET, period))
        db.execute("INSERT INTO camera_drift_buckets VALUES('cam-a',?,'frame-age',?,?,8,2,1)", (key, at, period))
        DeviceDegradation.evaluate(db, 'cam-a', key, 'frame-age', at, period)
    `, join(state, "activity.sqlite3"), cameraKey, source(".")]);
    await expect.poll(async () => (await api(address, "sensor-fusion")).gates[0].state, { timeout: 12_000 }).toBe("ready");
    const stamp = Date.now();
    const clip = hash(cameraKey + ":" + stamp).slice(0, 32);
    await copyFile(clipFixture, join(state, "clips", clip + ".mp4"));
    await writeFile(join(state, "clips", clip + ".json"), JSON.stringify({
      cameraKey, clipId: clip, occurredAt: stamp, durationSeconds: 6, eventOffsetSeconds: 2.07,
      expiresAt: Math.floor(stamp / 1000) + 600,
    }));
    await api(address, "telemetry", { type: "observation", id: "force-1", sensorId: door.id, eventType: "door-forced-open",
      occurredAt: new Date(stamp).toISOString(), summary: "Forced door at the fixture" }, adapter);
    radar.emit(new Date(stamp + 200).toISOString());
    const incidentId = (await api(address, "dispatch")).alerts[0].id;
    const posture = await enter(first, baseURL!, address, operatorToken);
    await posture.getByText("Device degradation warnings", { exact: true }).click();
    await expect(posture.getByText("1 active warnings", { exact: true })).toBeVisible();
    await posture.getByText("Gate camera · Warning", { exact: true }).click();
    await posture.getByText("Analysis frame age · Warning active", { exact: true }).click();
    await expect(posture.getByText(/Above baseline · Latest 4.0 s · Baseline 0.5 s/)).toBeVisible();
    await posture.getByText("Gate radar status", { exact: true }).click();
    await expect(posture.getByText("Gate radar · Reporting", { exact: true })).toBeVisible();
    let pane = await openDoor(first);
    await pane.getByRole("button", { name: "Take ownership" }).click();
    await summary(first, "Response checklist and handoff").click();
    await pane.getByRole("button", { name: "Draft handoff", exact: true }).click();
    await expect(pane.getByRole("heading", { name: "AI handoff draft" })).toBeVisible();
    expect((await api(address, "incident-history?" + new URLSearchParams({ incidentId }))).actions).toHaveLength(1);
    await pane.getByRole("button", { name: "Use draft in handoff" }).click();
    await pane.getByLabel("Handoff note").fill("Reviewed at the security desk.");
    await pane.getByRole("button", { name: "Save handoff" }).click();
    await expect(pane.getByText("Next action · Review the gate recording. · First operator")).toBeVisible();
    const checked = await api(address, "incident-history?" + new URLSearchParams({ incidentId }));
    expect(checked.actions.at(-1).action).toMatchObject({ kind: "handoff", note: "Reviewed at the security desk.",
      aiDraft: { sources: [expect.objectContaining({ eventId: "force-1" })] } });
    const jobId = checked.actions.at(-1).action.aiDraft.jobId;
    expect((await fetchSite(address, "ai-jobs/" + jobId, undefined, adminToken)).status).toBe(403);

    await enter(second, baseURL!, address, adminToken);
    pane = await openDoor(second);
    await summary(second, "Response checklist and handoff").click();
    await pane.getByRole("button", { name: "Accept handoff" }).click();
    await expect(pane.getByText(/Accepted by Supervisor/)).toBeVisible();
    await pane.getByRole("button", { name: "Replay timeline" }).click();
    await pane.getByRole("button", { name: "Review with AI" }).click();
    await pane.getByRole("button", { name: "Analyze event frames" }).click();
    await expect(pane.getByRole("heading", { name: "Draft observation · Occupancy observed" })).toBeVisible({ timeout: 20000 });
    await pane.getByRole("button", { name: /^Seek to .*Event frame/ }).click();
    await expect.poll(async () => pane.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThanOrEqual(2.19);
    await pane.getByRole("button", { name: "Back to incident" }).click();
    await expect(pane.getByText("Gate corroboration · Corroborated")).toBeVisible({ timeout: 45000 });
    await pane.getByText("Gate corroboration · Corroborated").click();
    await pane.getByRole("button", { name: "Confirmed", exact: true }).click();
    await expect(pane.getByText("Confirmed · Supervisor", { exact: true })).toBeVisible();
    await pane.getByRole("button", { name: /Radar motion in configured gate area/ }).click();
    await expect(pane.getByRole("heading", { name: "Radar motion in configured gate area" })).toBeVisible();
    expect(provider.requests.map(({ frames }) => frames.length)).toEqual([0, expect.any(Number)]);
    const draftSignals = JSON.parse(provider.requests[0].text[0]) as { type: string }[];
    expect(draftSignals.map(({ type }) => type)).toContain("rf-detection");
    expect(provider.requests[1].frames.length).toBeGreaterThan(1);
    expect(provider.requests[1].frames.length).toBeLessThanOrEqual(8);
    expect((await api(address, "dispatch")).alerts).toHaveLength(1);
    await testInfo.attach("Site intelligence fixture review", { contentType: "application/json", body: JSON.stringify({
      handoff: checked.actions.at(-1).action.aiDraft, frames: provider.requests[1].frames.length,
      radar: await api(address, "sensor-fusion?" + new URLSearchParams({ incidentId })),
    }) });
  } finally {
    await first.close(); await second.close();
    await stop(gateway?.child); await provider.close(); await radar.close();
    await rm(directory, { recursive: true, force: true });
  }
});

async function gateRadarFixture() {
  const { createServer } = await import("node:http");
  const tds = "http://www.onvif.org/ver10/device/wsdl", wsnt = "http://docs.oasis-open.org/wsn/b-2";
  const events = "http://www.onvif.org/ver10/events/wsdl", soap = "http://www.w3.org/2003/05/soap-envelope";
  let origin = "";
  let queued: string | undefined;
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    const date = new Date();
    const now = date.toISOString(), expires = new Date(date.getTime() + 60000).toISOString();
    let result: string;
    if (body.includes("GetServices")) {
      result = `<d:GetServicesResponse><d:Service><d:Namespace>${events}</d:Namespace><d:XAddr>${origin}/events</d:XAddr></d:Service></d:GetServicesResponse>`;
    } else if (body.includes("GetEventProperties")) {
      result = '<e:GetEventPropertiesResponse><wstop:TopicSet><axis:RadarSource><axis:MotionAlarm><axis:Channel0Profile1/></axis:MotionAlarm></axis:RadarSource></wstop:TopicSet></e:GetEventPropertiesResponse>';
    } else if (body.includes("GetSystemDateAndTime")) {
      result = `<d:GetSystemDateAndTimeResponse><d:SystemDateAndTime><t:UTCDateTime><t:Time><t:Hour>${date.getUTCHours()}</t:Hour><t:Minute>${date.getUTCMinutes()}</t:Minute><t:Second>${date.getUTCSeconds()}</t:Second></t:Time><t:Date><t:Year>${date.getUTCFullYear()}</t:Year><t:Month>${date.getUTCMonth() + 1}</t:Month><t:Day>${date.getUTCDate()}</t:Day></t:Date></t:UTCDateTime></d:SystemDateAndTime></d:GetSystemDateAndTimeResponse>`;
    } else if (body.includes("CreatePullPointSubscription")) {
      result = `<e:CreatePullPointSubscriptionResponse><e:SubscriptionReference><wsa:Address>${origin}/pull</wsa:Address></e:SubscriptionReference><e:CurrentTime>${now}</e:CurrentTime><e:TerminationTime>${expires}</e:TerminationTime></e:CreatePullPointSubscriptionResponse>`;
    } else if (body.includes("PullMessages")) {
      const stamp = queued;
      queued = undefined;
      const notification = stamp ? `<w:NotificationMessage><w:Topic>axis:RadarSource/MotionAlarm/Channel0Profile1</w:Topic><w:Message><t:Message UtcTime="${stamp}" PropertyOperation="Changed"><t:Data><t:SimpleItem Name="active" Value="true"/></t:Data></t:Message></w:Message></w:NotificationMessage>` : "";
      result = `<e:PullMessagesResponse>${notification}</e:PullMessagesResponse>`;
    } else { response.writeHead(404).end(); return; }
    response.writeHead(200, { "Content-Type": "application/soap+xml" }).end(`<s:Envelope xmlns:s="${soap}" xmlns:d="${tds}" xmlns:t="http://www.onvif.org/ver10/schema" xmlns:e="${events}" xmlns:w="${wsnt}" xmlns:wsa="http://www.w3.org/2005/08/addressing" xmlns:wstop="http://docs.oasis-open.org/wsn/t-1" xmlns:axis="http://www.axis.com/2009/event/topics"><s:Body>${result}</s:Body></s:Envelope>`);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  origin = "http://127.0.0.1:" + (server.address() as { port: number }).port;
  return { endpoint: origin + "/onvif", emit: (stamp: string) => { queued = stamp; },
    close: () => new Promise<void>((resolve) => { server.close(() => resolve()); server.closeAllConnections(); }) };
}

test("site intelligence automatically reviews a new incident and opens a protected recording from its key timestamp", async ({ browser, baseURL }) => {
  test.setTimeout(90_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-automatic-triage-"));
  const state = join(directory, "state"), roles = join(directory, "roles.json"), adapters = join(directory, "adapters.json");
  const settings = join(directory, "intelligence.json"), cameraKey = "3".repeat(48);
  const { localInferenceFixture } = await import("./fixtures/local-inference");
  const provider = await localInferenceFixture();
  const supervisor = await browser.newPage(), operator = await browser.newPage();
  let gateway: Awaited<ReturnType<typeof start>> | undefined;
  try {
    await writeFile(roles, JSON.stringify(roster));
    await writeFile(adapters, JSON.stringify({ door: adapter, [camera.id]: adapter }));
    await mkdir(join(state, "clips"), { recursive: true });
    await writeFile(join(state, "cameras.json"), JSON.stringify({ [camera.id]: { key: cameraKey, url: "rtsp://127.0.0.1/fixture" } }));
    await writeFile(settings, JSON.stringify({ ai: { provider: "on-premises", allowAutomatedTriage: true, localOrigin: provider.origin } }));
    gateway = await start(state, roles, ["--site-intelligence", settings, "--axis-recorder", "http://127.0.0.1:47999",
      "--telemetry-devices", adapters, "--require-camera-session"]);
    const { address } = gateway;
    const linked = { ...camera, cameraGatewayKind: "rtsp", cameraFeedUrl: address + "/streams/" + cameraKey + "/index.m3u8" };
    await config(address, "save", { configuration: { sensors: [linked, door], boundaries: [{ ...boundary, sensorIds: [camera.id, door.id] }] } });
    await config(address, "publish", { message: "Automatic triage fixture" });

    const posture = await enter(supervisor, baseURL!, address, adminToken);
    await posture.locator("summary", { hasText: "Automatic AI triage" }).click();
    const toggle = posture.getByRole("checkbox", { name: "Enable automatic AI triage" });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(toggle).toBeChecked();
    expect((await fetchSite(address, "automatic-triage", { enabled: false, revision: 1 }, operatorToken)).status).toBe(403);
    await supervisor.close();

    const stamp = Date.now();
    const motionAt = stamp + 1500;
    const clip = hash(cameraKey + ":" + motionAt).slice(0, 32);
    await copyFile(clipFixture, join(state, "clips", clip + ".mp4"));
    await writeFile(join(state, "clips", clip + ".json"), JSON.stringify({
      cameraKey, clipId: clip, occurredAt: motionAt, durationSeconds: 6, eventOffsetSeconds: 2.2,
      expiresAt: Math.floor(stamp / 1000) + 600,
    }));
    await api(address, "telemetry", { type: "observation", id: "automatic-door", sensorId: door.id, eventType: "door-forced-open",
      occurredAt: new Date(stamp).toISOString(), summary: "Forced door at the fixture" }, adapter);
    const incidentId = (await api(address, "dispatch")).alerts[0].id;
    await api(address, "telemetry", { type: "observation", id: "linked-motion", sensorId: camera.id, eventType: "motion",
      occurredAt: new Date(motionAt).toISOString(), summary: "Motion at the gate camera" }, adapter);
    const path = "automatic-triage?" + new URLSearchParams({ incidentId });
    expect((await api(address, path)).review.state).toBe("collecting");
    await expect.poll(async () => (await api(address, path)).review.state, { timeout: 45_000 }).toBe("ready");
    expect(provider.requests[0]?.frames).toEqual([]);
    const records = JSON.parse(provider.requests[0].text[0]);
    expect(records.map(({ eventId }: { eventId: string }) => eventId)).toEqual(["automatic-door", "linked-motion"]);

    await enter(operator, baseURL!, address, operatorToken);
    const pane = await openDoor(operator);
    const review = pane.getByRole("region", { name: "Automatic incident review" });
    const summary = review.getByLabel("Recorded timeline summary");
    await expect(summary.getByText("Forced-open door reported", { exact: false })).toBeVisible();
    await expect(summary.getByText("Camera motion reported", { exact: false })).toBeVisible();
    const timestamp = review.locator("ol").getByRole("button", { name: /Motion at the gate camera/ });
    expect(Date.parse((await timestamp.locator("time").getAttribute("datetime"))!)).toBe(motionAt);
    await timestamp.click();
    await expect(pane.getByRole("heading", { name: "Motion at the gate camera", exact: true })).toBeVisible();
    await expect(pane.locator("video")).toBeVisible();
    await expect.poll(() => pane.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime), { timeout: 8000 })
      .toBeGreaterThanOrEqual(2.19);
  } finally {
    await operator.close(); await supervisor.close();
    await stop(gateway?.child); await provider.close();
    await rm(directory, { recursive: true, force: true });
  }
});
