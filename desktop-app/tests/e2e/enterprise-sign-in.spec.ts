import { expect, test, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { constants, createHash, generateKeyPairSync, randomUUID, sign, verify, X509Certificate } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { request as relayRequest, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer } from "node:https";
import { createServer as reservePort } from "node:net";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const gateway = fileURLToPath(new URL("../../../services/camera-gateway/gateway.py", import.meta.url));
const portal = fileURLToPath(new URL("../../../services/camera-gateway/enterprise_portal.py", import.meta.url));
const dist = fileURLToPath(new URL("../../dist", import.meta.url));
const footage = fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url));
const python = process.env.PROXIGO_ENTERPRISE_PYTHON;
const mediaMtx = process.env.MEDIAMTX;
const subject = "816f30dd-e201-4402-9bbb-954ccd454f2e";
const admin = "9".repeat(64);
const adapter = "8".repeat(64);
const choose = (page: Page, label: string) => page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: label, exact: true });
const api = "/api/enterprise";
const route = (org: string, site: string) => `${api}/orgs/${org}/sites/${site}`;
const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
const requireAction = createRequire(import.meta.url);
const body = async (req: IncomingMessage) => {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString();
};

async function listen(server: ReturnType<typeof createServer> | ReturnType<typeof reservePort>) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return (server.address() as { port: number }).port;
}

async function ready(child: ChildProcess, pattern: RegExp) {
  return new Promise<RegExpMatchArray>((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => finish(new Error(`Fixture did not start. ${output}`)), 10_000);
    function finish(err?: Error, match?: RegExpMatchArray) {
      clearTimeout(timeout);
      child.stdout?.off("data", read); child.stderr?.off("data", read); child.off("exit", exit);
      if (match) resolve(match); else reject(err);
    }
    function read(chunk: Buffer) {
      output += chunk.toString();
      const match = output.match(pattern);
      if (match) finish(undefined, match);
    }
    function exit() { finish(new Error(`Fixture exited. ${output}`)); }
    child.stdout?.on("data", read); child.stderr?.on("data", read); child.once("exit", exit);
  });
}

function post(url: string, token: string, data: object) {
  return fetch(url, { method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

test("enterprise sign-in isolates organizations and live camera, incident, and site access", async ({ browser }) => {
  test.skip(!python, "Set PROXIGO_ENTERPRISE_PYTHON to Python with enterprise-requirements.txt installed and build the frontend.");
  test.setTimeout(110_000);
  const folder = await mkdtemp(join(tmpdir(), "proxigo-oidc-e2e-"));
  const certPath = join(folder, "cert.pem"), keyPath = join(folder, "key.pem");
  const clientCertPath = join(folder, "client-cert.pem"), clientKeyPath = join(folder, "client-key.pem");
  const children: ChildProcess[] = [];
  const servers: ReturnType<typeof createServer>[] = [];
  const contexts = await Promise.all([browser.newContext({ ignoreHTTPSErrors: true }), browser.newContext({ ignoreHTTPSErrors: true })]);
  let frontOrigin = "", directoryOrigin = "", portalPort = "";
  let rejectNextToken: "tenant" | "audience" | "signature" | "missing-oid" | "invalid-oid" | "unenrolled-oid" | undefined;
  let lastCallback = "";
  const organizations = {
    alpha: { name: "Alpha Security", tenant: "directory-alpha", keys: generateKeyPairSync("rsa", { modulusLength: 2048 }) },
    beta: { name: "Beta Security", tenant: "62b57f3c-20fe-4d62-b1e3-6cafef7fb8f2", keys: generateKeyPairSync("rsa", { modulusLength: 2048 }) },
  };
  type OrgId = keyof typeof organizations;
  const issuer = (org: OrgId) => `${directoryOrigin}/${org}`;
  const client = (org: OrgId) => org + "-global-view";
  const codes = new Map<string, { org: OrgId; query: URLSearchParams }>();
  const assertions = new Set<string>();
  let exchanges = 0;
  try {
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1", "-keyout", keyPath,
      "-out", certPath, "-addext", "subjectAltName=IP:127.0.0.1", "-addext", "basicConstraints=critical,CA:TRUE"], { stdio: "ignore" });
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Portal client fixture",
      "-keyout", clientKeyPath, "-out", clientCertPath], { stdio: "ignore" });
    const clientCertificate = new X509Certificate(await readFile(clientCertPath));
    const thumbprint = createHash("sha256").update(clientCertificate.raw).digest("base64url");
    const tls = { key: await readFile(keyPath), cert: await readFile(certPath) };
    const frontend = createServer(tls, (req, res) => {
      const upstream = relayRequest({ hostname: "127.0.0.1", port: portalPort, path: req.url, method: req.method, headers: req.headers }, (response) => {
        res.writeHead(response.statusCode!, response.headers);
        response.pipe(res);
      });
      upstream.on("error", () => res.writeHead(502).end());
      req.pipe(upstream);
    });
    servers.push(frontend);
    frontOrigin = "https://127.0.0.1:" + await listen(frontend);

    async function identity(req: IncomingMessage, res: ServerResponse) {
      const url = new URL(req.url!, directoryOrigin);
      const [, segment, ...resources] = url.pathname.split("/");
      const org = segment as OrgId, account = organizations[org];
      const resource = resources.join("/");
      const json = (payload: object) => res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(payload));
      if (!account) return res.writeHead(404).end();
      if (resource === ".well-known/openid-configuration") return json({ issuer: issuer(org), authorization_endpoint: issuer(org) + "/authorize",
        token_endpoint: issuer(org) + "/token", jwks_uri: issuer(org) + "/keys", code_challenge_methods_supported: ["S256"] });
      if (resource === "keys") return json({ keys: [{ ...account.keys.publicKey.export({ format: "jwk" }), kid: "directory-key", alg: "RS256", use: "sig" }] });
      if (resource === "authorize") {
        const query = url.searchParams;
        if (query.get("client_id") !== client(org) || query.get("redirect_uri") !== `${frontOrigin}${api}/callback/${org}`
          || query.get("response_type") !== "code" || query.get("scope") !== (org === "beta" ? "openid profile" : "openid")
          || query.get("code_challenge_method") !== "S256"
          || !query.get("code_challenge") || !query.get("nonce") || !query.get("state") || query.get("prompt") !== "login") return res.writeHead(400).end();
        const code = randomUUID();
        codes.set(code, { org, query });
        const callback = new URL(query.get("redirect_uri")!);
        callback.search = new URLSearchParams({ code, state: query.get("state")!, iss: issuer(org) }).toString();
        lastCallback = callback.href;
        return res.writeHead(200, { "Content-Type": "text/html" }).end(`<h1>${account.name} directory</h1><a href="${callback.href.replaceAll("&", "&amp;")}">Continue as operator</a>`);
      }
      if (resource === "token" && req.method === "POST") {
        const form = new URLSearchParams(await body(req));
        const code = form.get("code") ?? "";
        const request = codes.get(code);
        const challenge = createHash("sha256").update(form.get("code_verifier") ?? "").digest("base64url");
        const now = Math.floor(Date.now() / 1000);
        let confidential = req.headers.authorization === "Basic " + Buffer.from(client(org) + ":fixture-secret").toString("base64");
        if (org === "beta") {
          const [header, payload, signature] = (form.get("client_assertion") ?? "").split(".");
          if (!header || !payload || !signature) return res.writeHead(400).end();
          const decode = (segment: string) => JSON.parse(Buffer.from(segment, "base64url").toString());
          const jwtHeader = decode(header), claims = decode(payload);
          confidential = form.get("client_id") === client(org) && !form.has("client_secret") && !req.headers.authorization
            && form.get("client_assertion_type") === "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            && jwtHeader.alg === "PS256" && jwtHeader["x5t#S256"] === thumbprint
            && claims.iss === client(org) && claims.sub === client(org) && claims.aud === issuer(org) + "/token"
            && typeof claims.jti === "string" && !assertions.has(claims.jti)
            && claims.nbf <= now && claims.iat >= now - 10 && claims.exp > now && claims.exp <= now + 120
            && verify("sha256", Buffer.from(`${header}.${payload}`), { key: clientCertificate.publicKey,
              padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: constants.RSA_PSS_SALTLEN_DIGEST }, Buffer.from(signature, "base64url"));
          if (confidential) assertions.add(claims.jti);
        }
        if (!request || request.org !== org || !confidential || challenge !== request.query.get("code_challenge")
          || form.get("grant_type") !== "authorization_code" || form.get("redirect_uri") !== request.query.get("redirect_uri")) return res.writeHead(400).end();
        codes.delete(code);
        exchanges++;
        const rejection = rejectNextToken;
        rejectNextToken = undefined;
        const oid = rejection === "invalid-oid" ? "operator@example.test"
          : rejection === "unenrolled-oid" ? randomUUID() : subject.toUpperCase();
        const token = encode({ alg: "RS256", kid: "directory-key" }) + "." + encode({ iss: issuer(org),
          aud: rejection === "audience" ? "wrong-application" : client(org), sub: org === "alpha" ? subject : "pairwise-oidc-subject",
          oid: org === "beta" && rejection !== "missing-oid" ? oid : undefined,
          exp: now + 600, iat: now, nonce: request.query.get("nonce"), tid: rejection === "tenant" ? "wrong-tenant" : account.tenant });
        const signer = rejection === "signature" ? organizations.alpha.keys.privateKey : account.keys.privateKey;
        return json({ id_token: token + "." + sign("sha256", Buffer.from(token), signer).toString("base64url") });
      }
      res.writeHead(404).end();
    }
    const directory = createServer(tls, (req, res) => { void identity(req, res); });
    servers.push(directory);
    directoryOrigin = "https://127.0.0.1:" + await listen(directory);

    let source = "rtsp://127.0.0.1:9/camera";
    if (mediaMtx) {
      const reserve = reservePort();
      const port = await listen(reserve);
      await new Promise<void>((resolve) => reserve.close(() => resolve()));
      const config = join(folder, "mediamtx.yml");
      await writeFile(config, `rtspAddress: 127.0.0.1:${port}\nrtspTransports: [tcp]\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  all_others:\n`);
      source = `rtsp://127.0.0.1:${port}/camera`;
      const server = spawn(mediaMtx, [config], { stdio: ["ignore", "pipe", "pipe"] });
      children.push(server);
      await ready(server, /\[RTSP\].*(?:started|listener opened)/);
      const publisher = spawn("ffmpeg", ["-re", "-stream_loop", "-1", "-i", footage, "-an", "-c:v", "libx264", "-r", "15", "-preset",
        "ultrafast", "-tune", "zerolatency", "-g", "15", "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", source],
        { stdio: ["ignore", "ignore", "pipe"] });
      children.push(publisher);
      await ready(publisher, /Output #0, rtsp/);
    }

    async function createSite(org: OrgId, siteId: string, name: string, keyChar: string, member = true) {
      const id = org + "-" + siteId;
      const account = organizations[org], proxyToken = createHash("sha256").update(id).digest("hex");
      const key = keyChar.repeat(48), hidden = "f".repeat(48);
      const accessFile = join(folder, id + "-access.json"), devicesFile = join(folder, id + "-devices.json"), state = join(folder, id);
      await mkdir(state);
      await writeFile(join(state, "cameras.json"), JSON.stringify({ camera: { key, url: source }, hidden: { key: hidden, url: source } }));
      await writeFile(accessFile, JSON.stringify({ site: name, siteId, organization: { id: org, name: account.name, directoryTenant: account.tenant },
        operators: { admin: { name: "Setup Admin", role: "org-admin", cameras: "*", tokenSha256: createHash("sha256").update(admin).digest("hex") },
          ...(member ? { [subject]: { name: org === "alpha" ? "Alice Alpha" : "Bailey Beta", role: "operator", cameras: ["camera"] } } : {}) } }));
      await writeFile(devicesFile, JSON.stringify({ door: adapter, "hidden-door": adapter, camera: adapter }));
      const process = spawn("python3", [gateway, "--port", "0", "--allow-source", "127.0.0.1/32", "--state-dir", state, "--site-access",
        accessFile, "--telemetry-devices", devicesFile, "--require-camera-session"], { env: { ...globalThis.process.env, PROXIGO_IDENTITY_PROXY_TOKEN: proxyToken },
        stdio: ["ignore", "pipe", "pipe"] });
      children.push(process);
      const origin = "http://127.0.0.1:" + (await ready(process, /Camera gateway listening on 127\.0\.0\.1:(\d+)/))[1];
      const position = [-122.3, 37.78];
      const cameras = [["camera", key, `${name} camera`], ["hidden", hidden, `${name} restricted camera`]].map(([id, stream, cameraName]) =>
        ({ id, name: cameraName, modality: "vision", position, cameraGatewayKind: "rtsp", cameraFeedUrl: `${origin}/streams/${stream}/index.m3u8` }));
      const doors = ["door", "hidden-door"].map((id, index) => ({ id, name: `${name} ${id}`, modality: "access", position, linkedCameraId: cameras[index].id }));
      const configuration = { sensors: [...cameras, ...doors], boundaries: [{ id: "yard", name: `${name} yard`, kind: "Restricted",
        enabled: true, sensorIds: ["door", "camera", "hidden-door"], vertices: [[-122.31, 37.77], [-122.29, 37.77], [-122.29, 37.79], [-122.31, 37.79]] }] };
      const save = await post(origin + "/api/site/configuration", admin, { operation: "save", draftRevision: 0, publishedVersion: 0, configuration });
      expect(save.status, await save.clone().text()).toBe(200);
      const publish = await post(origin + "/api/site/configuration", admin, { operation: "publish", publishedVersion: 0,
        draftRevision: (await save.json()).draftRevision, message: "Enterprise fixture" });
      expect(publish.status).toBe(200);
      for (const sensorId of ["door", "hidden-door"]) {
        const event = await post(origin + "/api/site/telemetry", adapter, { type: "observation", sensorId, id: "door-event", occurredAt: new Date().toISOString(),
          eventType: "door-forced-open", summary: `${name} ${sensorId === "door" ? "door forced open" : "restricted incident"}` });
        expect(event.status).toBe(200);
      }
      return { origin, proxyToken, key, hidden };
    }
    const [aDock, aAnnex, aRestricted, bDock] = await Promise.all([
      createSite("alpha", "dock", "Alpha Dock", "a"), createSite("alpha", "annex", "Alpha Annex", "b"),
      createSite("alpha", "restricted", "Alpha Private", "c", false), createSite("beta", "dock", "Beta Dock", "d"),
    ]);
    const config = join(folder, "portal.json");
    const provider = (org: OrgId, sites: object) => ({ issuer: issuer(org), directoryTenant: organizations[org].tenant, tenantClaim: "tid", clientId: client(org),
      ...(org === "alpha" ? { clientSecret: "fixture-secret" } : { tokenEndpointAuthMethod: "private_key_jwt", subjectClaim: "oid",
        clientCertificate: clientCertPath, clientPrivateKey: clientKeyPath }), sites });
    await writeFile(config, JSON.stringify({ publicOrigin: frontOrigin, organizations: { alpha: provider("alpha", { dock: aDock, annex: aAnnex, restricted: aRestricted }),
      beta: provider("beta", { dock: bDock }) } }));
    const service = spawn(python!, [portal, "--port", "0", "--frontend", dist, "--state", join(folder, "portal.sqlite3"), "--config", config],
      { env: { ...process.env, SSL_CERT_FILE: certPath }, stdio: ["ignore", "pipe", "pipe"] });
    children.push(service);
    portalPort = (await ready(service, /Enterprise portal listening on .*:(\d+)/))[1];

    const [alpha, beta] = await Promise.all(contexts.map((context) => context.newPage()));
    await Promise.all([alpha, beta].map((page) => page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"))));
    const signedOut = (page: Page) => page.getByRole("button", { name: "Sign in with your organization" });
    async function startLogin(page: Page, org: OrgId) {
      await page.goto(frontOrigin);
      await expect(signedOut(page)).toBeVisible();
      await page.getByLabel("Organization ID").fill(org);
      await signedOut(page).click();
      await expect(page.getByRole("heading", { name: organizations[org].name + " directory" })).toBeVisible();
    }
    const enter = (page: Page) => page.getByRole("link", { name: "Continue as operator" }).click();
    const browserRequest = (page: Page, path: string, method = "GET") => page.evaluate(async ({ path, method }) => {
      const response = await fetch(path, { method, ...(method === "POST" ? { body: "{}", headers: { "Content-Type": "application/json" } } : {}) });
      return { status: response.status, data: await response.text(), cache: response.headers.get("cache-control") };
    }, { path, method });
    await alpha.goto(frontOrigin);
    await expect(signedOut(alpha)).toBeVisible();
    expect((await browserRequest(alpha, route("alpha", "dock") + "/api/site/monitoring")).status).toBe(401);
    await expect(choose(alpha, "Cameras")).toHaveCount(0);

    await startLogin(alpha, "alpha");
    const alphaCallback = lastCallback;
    await beta.goto(alphaCallback);
    await expect(beta.getByRole("alert")).toContainText("sign-in could not be completed");
    expect(exchanges).toBe(0);
    await enter(alpha);
    await expect(alpha.getByText("Alpha Security · Alpha Dock")).toBeVisible();

    for (const rejected of ["tenant", "audience", "signature", "missing-oid", "invalid-oid"] as const) {
      rejectNextToken = rejected;
      await startLogin(beta, "beta");
      await enter(beta);
      await expect(beta.getByRole("alert")).toContainText("sign-in could not be completed");
      expect((await browserRequest(beta, api + "/session")).status).toBe(401);
    }
    rejectNextToken = "unenrolled-oid";
    await startLogin(beta, "beta");
    await enter(beta);
    await expect(beta.getByText("No sites are currently available to this account.")).toBeVisible();
    await beta.getByRole("button", { name: "Sign out", exact: true }).click();
    await startLogin(beta, "beta");
    await enter(beta);
    await expect(beta.getByText("Beta Security · Beta Dock")).toBeVisible();
    expect(exchanges).toBe(8);
    expect(assertions.size).toBe(7);

    for (const [page, org, site, own, foreign] of [[alpha, "alpha", "Alpha Dock", aDock, bDock], [beta, "beta", "Beta Dock", bDock, aDock]] as const) {
      const otherOrg = org === "alpha" ? "beta" : "alpha";
      const ownPath = route(org, "dock"), foreignPath = route(otherOrg, "dock");
      const session = (await page.context().cookies(frontOrigin)).find((cookie) => cookie.name === "__Host-proxigo-session");
      expect(session).toMatchObject({ httpOnly: true, secure: true, sameSite: "Lax", path: "/" });
      expect(await page.evaluate(() => document.cookie)).not.toContain("proxigo-session");
      const directory = await browserRequest(page, api + "/session");
      expect(directory.status).toBe(200);
      expect(directory.cache).toBe("no-store");
      expect(JSON.parse(directory.data).sites.map(({ site }: { site: string }) => site)).toEqual(org === "alpha" ? ["Alpha Dock", "Alpha Annex"] : ["Beta Dock"]);
      expect(directory.data).not.toContain(foreign.proxyToken);
      await choose(page, "Alerts").click();
      await page.getByRole("region", { name: "Alerts", exact: true }).getByRole("button", { name: new RegExp(`^${site} door forced open`) }).first().click();
      const inspector = page.getByRole("complementary", { name: "Monitor and review" });
      await expect(inspector.getByRole("heading", { name: `${site} door forced open` })).toBeVisible();
      await expect(page.getByText(/restricted incident|Alpha Private/)).toHaveCount(0);
      const monitoring = await browserRequest(page, ownPath + "/api/site/monitoring");
      expect(monitoring.status).toBe(200);
      expect(JSON.parse(monitoring.data).alerts.map((alert: { observation: { summary: string } }) => alert.observation.summary)).toEqual([`${site} door forced open`]);
      await choose(page, "Cameras").click();
      const cameras = page.getByRole("region", { name: "Cameras", exact: true });
      await expect(cameras.getByText(site + " camera", { exact: true }).first()).toBeVisible();
      await expect(cameras.getByText(/restricted camera/)).toHaveCount(0);
      const foreignName = org === "alpha" ? "Beta Dock" : "Alpha Dock";
      await expect(page.getByText(new RegExp(foreignName))).toHaveCount(0);
      if (mediaMtx) {
        const video = cameras.locator("video").first();
        await expect.poll(() => video.evaluate((item: HTMLVideoElement) => item.videoWidth), { timeout: 18_000 }).toBeGreaterThan(0);
        expect((await browserRequest(page, `${ownPath}/streams/${own.key}/index.m3u8`)).status).toBe(200);
      }
      expect((await browserRequest(page, ownPath + "/api/site/analytics-review")).status).toBe(200);
      expect((await browserRequest(page, ownPath + "/api/site/analytics-report")).status).toBe(200);
      expect((await browserRequest(page, ownPath + "/api/site/inspection-results")).status).toBe(200);
      for (const route of ["commissioning-runs", "dock-schedules", "door-passages", "camera-native", "dispatch", "cases", "operations", "ai-jobs", "automatic-triage", "device-degradation", "sensor-fusion"]) {
        expect((await browserRequest(page, ownPath + "/api/site/" + route)).status).toBe(200);
        expect((await browserRequest(page, foreignPath + "/api/site/" + route)).status).toBe(404);
      }
      await expect.poll(async () => {
        const result = await browserRequest(page, ownPath + "/api/site/evidence-search");
        expect(result.status).toBe(200);
        expect(result.data).not.toContain("restricted incident");
        expect(result.data).not.toContain(foreignName);
        return JSON.parse(result.data).results.length;
      }).toBeGreaterThan(0);
      expect((await browserRequest(page, foreignPath + "/api/site/evidence-search")).status).toBe(404);
      expect((await browserRequest(page, foreignPath + "/api/site/case-export", "POST")).status).toBe(404);
      expect((await browserRequest(page, foreignPath + "/api/site/ai-jobs", "POST")).status).toBe(404);
      expect((await browserRequest(page, foreignPath + "/api/site/automatic-triage", "POST")).status).toBe(404);
      expect((await browserRequest(page, foreignPath + "/api/site/ai-jobs/" + "a".repeat(32))).status).toBe(404);
      for (const path of ["/api/site/session", "/api/site/monitoring", "/api/site/activity", "/api/site/telemetry", "/api/site/accounts", "/api/site/analytics-review",
        "/api/site/analytics-report", "/api/site/inspection-results",
        `/streams/${foreign.key}/index.m3u8`, `/streams/${foreign.key}/recording.json?at=1`]) {
        expect((await browserRequest(page, foreignPath + path)).status, path).toBe(404);
      }
      expect((await browserRequest(page, foreignPath + "/api/site/incident", "POST")).status).toBe(404);
      expect((await browserRequest(page, foreignPath + "/api/site/analytics-review", "POST")).status).toBe(404);
      expect((await browserRequest(page, `${ownPath}/streams/${foreign.key}/index.m3u8`)).status).toBe(403);
      expect((await browserRequest(page, `${ownPath}/streams/${own.hidden}/status.json`)).status).toBe(403);
      expect((await browserRequest(page, ownPath + "/api/site/accounts")).status).toBe(403);
    }
    expect((await browserRequest(alpha, route("alpha", "restricted") + "/api/site/monitoring")).status).toBe(404);
    const sitePicker = alpha.getByRole("combobox", { name: "Organization site" });
    await expect(sitePicker.getByRole("option")).toHaveText(["Alpha Dock", "Alpha Annex"]);
    const desktop = alpha.viewportSize()!;
    await alpha.setViewportSize({ width: 320, height: 740 });
    const brand = await alpha.locator(".gv-brandbar").boundingBox();
    const actions = await alpha.getByRole("group", { name: "Site actions" }).boundingBox();
    expect(brand!.x + brand!.width).toBeLessThan(actions!.x);
    await alpha.setViewportSize(desktop);
    await sitePicker.selectOption({ label: "Alpha Annex" });
    await expect(alpha.getByText("Alpha Security · Alpha Annex")).toBeVisible();
    await choose(alpha, "Alerts").click();
    await expect(alpha.getByRole("region", { name: "Alerts", exact: true }).getByRole("button", { name: /^Alpha Annex door forced open/ }).first()).toBeVisible();

    const savedCookie = (await alpha.context().cookies(frontOrigin)).find((cookie) => cookie.name === "__Host-proxigo-session")!;
    await alpha.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(signedOut(alpha)).toBeVisible();
    await expect(alpha.getByText("Alpha Security · Alpha Annex")).toHaveCount(0);
    await alpha.context().addCookies([savedCookie]);
    expect((await browserRequest(alpha, api + "/session")).status).toBe(401);
    await alpha.context().clearCookies();
    await alpha.goto(alphaCallback);
    await expect(alpha.getByRole("alert")).toContainText("sign-in could not be completed");
    await startLogin(alpha, "beta");
    await enter(alpha);
    await expect(alpha.getByText("Beta Security · Beta Dock")).toBeVisible();
    expect((await browserRequest(alpha, route("alpha", "dock") + "/api/site/monitoring")).status).toBe(404);
    expect((await post(bDock.origin + "/api/site/accounts", admin, { operation: "disable", subject })).status).toBe(200);
    await expect(alpha.getByText("No sites are currently available to this account.")).toBeVisible({ timeout: 10_000 });
    await expect(beta.getByText("No sites are currently available to this account.")).toBeVisible({ timeout: 10_000 });
    expect((await browserRequest(beta, route("beta", "dock") + "/api/site/monitoring")).status).toBe(404);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    for (const server of servers) { server.closeAllConnections(); server.close(); }
    await Promise.all(children.filter((child) => child.pid && child.exitCode === null && child.signalCode === null).map(async (child) => {
      const exited = once(child, "exit");
      child.kill();
      const timer = setTimeout(() => child.kill("SIGKILL"), 3000);
      try { await exited; } finally { clearTimeout(timer); }
    }));
    await rm(folder, { recursive: true, force: true });
  }
});

test("production email, Google, and Microsoft require fresh authenticator proof", async ({ browser }) => {
  test.skip(!python, "Set PROXIGO_ENTERPRISE_PYTHON and build the frontend.");
  test.setTimeout(90_000);
  const directory = await mkdtemp(join(tmpdir(), "proxigo-production-login-"));
  const cert = join(directory, "cert.pem"), key = join(directory, "key.pem");
  const servers: ReturnType<typeof createServer>[] = [];
  const children: ChildProcess[] = [];
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const { onExecutePostLogin: requireAuthenticator } = requireAction("../../../infra/auth0-global-view/require-authenticator.cjs");
  const { onExecutePostLogin: attestAuthenticator } = requireAction("../../../infra/auth0-global-view/attest-authenticator.cjs");
  const clientId = "fixture-production-web-app", clientSecret = "fixture-web-secret";
  const tenants = { alpha: "org_FixtureAlpha", beta: "org_FixtureBeta" };
  const connections = { email: "fixture-email", google: "google-oauth2", microsoft: "windowslive" };
  const users = { "fixture-email": "auth0|operator", "google-oauth2": "google-oauth2|operator", windowslive: "windowslive|operator" };
  const enrolled = new Set<string>();
  const mfa = "https://proxigo.ai/claims/mfa_at";
  const connectionClaim = "https://proxigo.ai/claims/connection";
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  type Connection = keyof typeof users;
  type Transaction = { query: URLSearchParams; connection: Connection; mfaComplete: boolean };
  const transactions = new Map<string, Transaction>();
  let corrupt: "missing" | "stale" | "connection" | "tenant" | undefined;
  let portalPort = "", issuer = "", origin = "";
  try {
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
      "-keyout", key, "-out", cert, "-addext", "subjectAltName=IP:127.0.0.1", "-addext", "basicConstraints=critical,CA:TRUE"], { stdio: "ignore" });
    const tls = { key: await readFile(key), cert: await readFile(cert) };
    const front = createServer(tls, (req, res) => {
      const upstream = relayRequest({ hostname: "127.0.0.1", port: portalPort, path: req.url, method: req.method, headers: req.headers }, (response) => {
        res.writeHead(response.statusCode!, response.headers);
        response.pipe(res);
      });
      upstream.on("error", () => res.writeHead(502).end());
      req.pipe(upstream);
    });
    servers.push(front);
    origin = "https://127.0.0.1:" + await listen(front);

    function brokerEvent(tx: Transaction, verified = false) {
      const user = users[tx.connection];
      return { client: { client_id: clientId }, secrets: { GLOBAL_VIEW_CLIENT_ID: clientId },
        connection: { name: tx.connection, strategy: tx.connection === connections.email ? "auth0" : tx.connection },
        organization: { id: tx.query.get("organization") },
        user: { email_verified: true, enrolledFactors: enrolled.has(user) ? [{ type: "otp" }] : [] },
        authentication: { methods: verified ? [{ name: "mfa", type: "otp", timestamp: new Date().toISOString() }] : [] } };
    }

    const broker = createServer(tls, async (req, res) => {
      const url = new URL(req.url!, issuer);
      const json = (value: object) => res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(value));
      const html = (value: string, status = 200) => res.writeHead(status, { "Content-Type": "text/html" }).end(value);
      if (url.pathname === "/.well-known/openid-configuration") return json({ issuer, authorization_endpoint: issuer + "authorize",
        token_endpoint: issuer + "token", jwks_uri: issuer + "keys", code_challenge_methods_supported: ["S256"] });
      if (url.pathname === "/keys") return json({ keys: [{ ...publicKey.export({ format: "jwk" }), kid: "broker-test", alg: "RS256" }] });
      if (url.pathname === "/authorize") {
        const query = url.searchParams;
        const org = Object.entries(tenants).find(([, tenant]) => query.get("organization") === tenant)?.[0];
        const connection = (query.get("connection") || (query.get("invitation") === "fixture-invitation" ? connections.google : "")) as Connection;
        if (!org || !Object.hasOwn(users, connection) || query.get("client_id") !== clientId || query.get("scope") !== "openid"
          || query.get("redirect_uri") !== origin + api + "/callback/" + org || query.get("prompt") !== "login"
          || query.get("code_challenge_method") !== "S256" || !query.get("state") || !query.get("nonce")) return res.writeHead(400).end();
        const ticket = randomUUID();
        transactions.set(ticket, { query, connection, mfaComplete: false });
        const heading = connection === connections.email ? "Email and password" : connection === connections.google ? "Google sign-in" : "Microsoft sign-in";
        const fields = connection === connections.email ? '<label>Email <input type="email" required></label><label>Password <input type="password" required></label>' : "";
        return html(`<h1>${heading}</h1><form action="/authenticate">${fields}<input type="hidden" name="ticket" value="${ticket}"><button>Continue</button></form>`);
      }
      if (url.pathname === "/authenticate") {
        const ticket = url.searchParams.get("ticket") ?? "", tx = transactions.get(ticket);
        if (!tx) return res.writeHead(400).end();
        let step = "";
        await requireAuthenticator(brokerEvent(tx), { access: { deny: () => { step = "denied"; } }, authentication: {
          challengeWith: ({ type }: { type: string }) => { if (type === "otp") step = "Authenticator challenge"; },
          enrollWith: ({ type }: { type: string }) => { if (type === "otp") step = "Set up authenticator"; },
        } });
        if (!step || step === "denied") return res.writeHead(400).end();
        return html(`<h1>${step}</h1><form action="/verify" method="post"><input type="hidden" name="ticket" value="${ticket}"><label>Authenticator code <input name="otp"></label><button>Verify</button></form>`);
      }
      if (url.pathname === "/verify" && req.method === "POST") {
        const form = new URLSearchParams(await body(req));
        const ticket = form.get("ticket") ?? "", tx = transactions.get(ticket);
        if (!tx || form.get("otp") !== "123456") return html('<h1>Authenticator code rejected</h1>', 401);
        tx.mfaComplete = true;
        enrolled.add(users[tx.connection]);
        const callback = new URL(tx.query.get("redirect_uri")!);
        callback.search = new URLSearchParams({ code: ticket, state: tx.query.get("state")! }).toString();
        return res.writeHead(303, { Location: callback.href }).end();
      }
      if (url.pathname === "/token" && req.method === "POST") {
        const form = new URLSearchParams(await body(req));
        const ticket = form.get("code") ?? "", tx = transactions.get(ticket);
        if (!tx || !tx.mfaComplete || form.get("client_id") !== clientId || form.get("client_secret") !== clientSecret
          || form.get("redirect_uri") !== tx.query.get("redirect_uri") || form.get("grant_type") !== "authorization_code"
          || createHash("sha256").update(form.get("code_verifier") ?? "").digest("base64url") !== tx.query.get("code_challenge")) return res.writeHead(400).end();
        transactions.delete(ticket);
        const now = Math.floor(Date.now() / 1000);
        const claims: Record<string, unknown> = { iss: issuer, aud: clientId, sub: users[tx.connection], nonce: tx.query.get("nonce"),
          org_id: tx.query.get("organization"), iat: now, exp: now + 300 };
        let denied = false;
        await attestAuthenticator(brokerEvent(tx, true), { access: { deny: () => { denied = true; } }, idToken: {
          setCustomClaim: (name: string, value: unknown) => { claims[name] = value; },
        } });
        if (denied) return res.writeHead(400).end();
        if (corrupt === "missing") delete claims[mfa];
        if (corrupt === "stale") claims[mfa] = now - 600;
        if (corrupt === "connection") claims[connectionClaim] = "some-other-connection";
        if (corrupt === "tenant") claims.org_id = tenants.beta;
        corrupt = undefined;
        const token = encode({ alg: "RS256", kid: "broker-test" }) + "." + encode(claims);
        return json({ id_token: token + "." + sign("sha256", Buffer.from(token), privateKey).toString("base64url") });
      }
      res.writeHead(404).end();
    });
    servers.push(broker);
    issuer = "https://127.0.0.1:" + await listen(broker) + "/";

    async function site(org: keyof typeof tenants) {
      const access = join(directory, `access-${org}.json`);
      const proxyToken = createHash("sha256").update("fixture-" + org).digest("hex");
      await writeFile(access, JSON.stringify({ site: "Office", siteId: "office", organization: {
        id: org, name: org === "alpha" ? "Alpha" : "Beta", directoryTenant: tenants[org],
      }, operators: Object.fromEntries(Object.values(users).map((id, index) => [id, { name: "Operator " + index, role: "operator", cameras: [] }])) }));
      const service = spawn(python!, [gateway, "--port", "0", "--allow-source", "127.0.0.1/32", "--state-dir", join(directory, org),
        "--site-access", access, "--require-camera-session"], { env: { ...process.env, PROXIGO_IDENTITY_PROXY_TOKEN: proxyToken } });
      children.push(service);
      const port = (await ready(service, /Camera gateway listening on .*:(\d+)/))[1];
      return { origin: "http://127.0.0.1:" + port, proxyToken };
    }
    const [alpha, beta] = await Promise.all([site("alpha"), site("beta")]);
    const org = (directoryTenant: string, office: object, auth0Connections: object) => ({ issuer, clientId, clientSecret,
      tokenEndpointAuthMethod: "client_secret_post", directoryTenant, auth0Connections, sites: { office } });
    const config = join(directory, "portal.json");
    await writeFile(config, JSON.stringify({ publicOrigin: origin, production: true, organizations: {
      alpha: org(tenants.alpha, alpha, connections), beta: org(tenants.beta, beta, { google: connections.google }),
    } }));
    const service = spawn(python!, [portal, "--port", "0", "--frontend", dist, "--state", join(directory, "portal.sqlite3"), "--config", config],
      { env: { ...process.env, SSL_CERT_FILE: cert } });
    children.push(service);
    portalPort = (await ready(service, /Enterprise portal listening on .*:(\d+)/))[1];

    const page = await context.newPage();
    const get = (path: string) => context.request.get(origin + path);
    async function login(button: string, provider: string, first = false) {
      await page.goto(origin);
      await page.getByLabel("Organization ID").fill("alpha");
      await page.getByRole("button", { name: button, exact: true }).click();
      await expect(page.getByRole("heading", { name: provider })).toBeVisible();
      if (provider === "Email and password") {
        await page.getByLabel("Email").fill("operator@example.test");
        await page.getByLabel("Password").fill("fixture-password");
      }
      await page.getByRole("button", { name: "Continue", exact: true }).click();
      await expect(page.getByRole("heading", { name: first ? "Set up authenticator" : "Authenticator challenge" })).toBeVisible();
      expect((await get(api + "/session")).status()).toBe(401);
    }
    async function verifyCode(code = "123456") {
      await page.getByLabel("Authenticator code").fill(code);
      await page.getByRole("button", { name: "Verify", exact: true }).click();
    }
    async function verifySiteAndSignOut() {
      await expect(page.getByText("Alpha · Office")).toBeVisible();
      const result = await get(api + "/session");
      expect((await result.json()).organizationId).toBe("alpha");
      expect((await get(route("beta", "office") + "/api/site/session")).status()).toBe(404);
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await expect.poll(async () => (await get(api + "/session")).status()).toBe(401);
    }

    await page.goto(origin);
    await page.getByLabel("Organization ID").fill("beta");
    await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue with email" })).toHaveCount(0);
    const invalid = await context.request.post(origin + api + "/login", { headers: { Origin: origin },
      form: { organization: "beta", method: "microsoft" }, maxRedirects: 0 });
    expect(invalid.headers().location).toBe("/?sign-in=failed");

    await login("Continue with email", "Email and password", true);
    await verifyCode("000000");
    await expect(page.getByRole("heading", { name: "Authenticator code rejected" })).toBeVisible();
    expect((await get(api + "/session")).status()).toBe(401);
    for (const [button, provider] of [["Continue with email", "Email and password"],
      ["Continue with Google", "Google sign-in"], ["Continue with Microsoft", "Microsoft sign-in"]]) {
      await login(button, provider, true);
      await verifyCode();
      await verifySiteAndSignOut();
    }
    for (const mode of ["missing", "stale", "connection", "tenant"] as const) {
      corrupt = mode;
      await login("Continue with Microsoft", "Microsoft sign-in");
      await verifyCode();
      await expect(page.getByRole("alert")).toContainText("sign-in could not be completed");
      expect((await get(api + "/session")).status()).toBe(401);
    }
    await page.goto(origin + api + "/invite?organization=" + tenants.alpha + "&invitation=fixture-invitation");
    await expect(page.getByRole("heading", { name: "Google sign-in" })).toBeVisible();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Authenticator challenge" })).toBeVisible();
    await verifyCode();
    await verifySiteAndSignOut();
  } finally {
    await context.close();
    for (const server of servers) { server.closeAllConnections(); server.close(); }
    await Promise.all(children.filter((child) => child.exitCode === null && child.signalCode === null).map(async (child) => {
      const exited = once(child, "exit");
      child.kill();
      const timer = setTimeout(() => child.kill("SIGKILL"), 3000);
      try { await exited; } finally { clearTimeout(timer); }
    }));
    await rm(directory, { recursive: true, force: true });
  }
});
