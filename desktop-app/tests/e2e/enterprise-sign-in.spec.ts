import { expect, test, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { constants, createHash, generateKeyPairSync, randomUUID, sign, verify, X509Certificate } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { request as relayRequest, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer } from "node:https";
import { createServer as reservePort } from "node:net";
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
