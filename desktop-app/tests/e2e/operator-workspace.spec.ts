import AxeBuilder from "@axe-core/playwright";
import { chromium, expect, test, type Locator, type Page } from "@playwright/test";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const viewKey = "proxigo.global-view.view.v1";
const review = (page: Page) => page.getByRole("complementary", { name: "Monitor and review" });
const sections = (page: Page) => page.getByRole("navigation", { name: "Workspace panels" });
const button = (scope: Page | Locator, name: string | RegExp) => scope.getByRole("button", { name, exact: typeof name === "string" });
const heading = (scope: Page | Locator, name: string) => scope.getByRole("heading", { name, exact: true });

async function activate(control: Locator) {
  await control.focus();
  await control.press("Enter");
}

async function importJSON(page: Page, data: unknown) {
  await page.getByLabel("Observation JSON file").setInputFiles({
    name: "telemetry.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(data)),
  });
}

async function checkAxe(page: Page) {
  const { violations } = await new AxeBuilder({ page }).include(".gv-app")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(violations.map(({ id, nodes }) => ({ id, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) }))).toEqual([]);
}

test("keyboard map, Site list, search, and remembered workspace", async ({ page }) => {
  await page.goto("/");
  const map = page.getByRole("region", { name: "Site map" });
  await expect(button(map, "Fit demo site")).toBeEnabled();
  await expect.poll(() => map.evaluate((node) => [node.clientWidth, node.clientHeight])).toEqual(
    [page.viewportSize()!.width, page.viewportSize()!.height],
  );
  await checkAxe(page);

  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to site content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(heading(page, "Alerts")).toBeFocused();
  const searchButton = button(page, "Search workspace");
  await activate(searchButton);
  const dialog = page.getByRole("dialog", { name: "Search workspace" });
  const combobox = dialog.getByRole("combobox");
  await expect(combobox).toBeFocused();
  await combobox.fill("camera");
  const active = dialog.getByRole("option", { selected: true });
  const firstOption = await active.getAttribute("id");
  await combobox.press("ArrowDown");
  await expect(active).not.toHaveAttribute("id", firstOption!);
  await expect(combobox).toHaveAttribute("aria-activedescendant", (await active.getAttribute("id"))!);
  await combobox.fill("camera not in this site");
  await expect(combobox).toHaveAttribute("aria-expanded", "false");
  await expect(combobox).not.toHaveAttribute("aria-activedescendant", /.+/);
  await combobox.press("Escape");
  await expect(searchButton).toBeFocused();

  await activate(searchButton);
  await combobox.fill("North perimeter camera");
  await checkAxe(page);
  await combobox.press("Enter");
  await expect(heading(review(page), "North perimeter camera")).toBeFocused();
  await expect(heading(page, "Devices")).toBeVisible();
  await activate(button(page, "Map"));
  await expect(heading(review(page), "North perimeter camera")).toBeVisible();
  await expect(button(page, "Map")).toBeFocused();
  await activate(button(page, "Site list"));
  await page.reload();
  await expect(heading(page, "Alerts")).toBeVisible();
  await expect(map).toHaveCount(0);
  expect(await page.evaluate((key) => localStorage.getItem(key), viewKey)).toBe("list");
});

test("one left rail stays in place across workspaces", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 740 });
  await page.goto("/");
  const rail = sections(page);
  const originalRail = await rail.elementHandle();
  const position = await rail.boundingBox();
  const labels = ["Map", "Site list", "Cameras", "Alerts", "Devices", "Boundaries", "Posture", "Map layers", "Search workspace"];
  const camera = page.getByRole("region", { name: "Cameras", exact: true });
  const map = page.getByRole("region", { name: "Site map", exact: true });
  await expect(rail.getByRole("button", { name: "Map", exact: true })).toHaveAttribute("aria-pressed", "true");

  async function checkRail() {
    await expect(page.getByRole("navigation")).toHaveCount(1);
    expect(await rail.evaluate((element, origin) => element === origin, originalRail)).toBe(true);
    expect(await rail.boundingBox()).toEqual(position);
    await expect(rail.getByRole("button")).toHaveCount(labels.length);
    for (const [index, name] of labels.entries()) {
      await expect(rail.getByRole("button").nth(index)).toHaveAccessibleName(name);
    }
  }

  await activate(button(rail, "Cameras"));
  await expect(camera.getByRole("heading", { name: "Live wall" })).toBeFocused();
  await expect(button(rail, "Cameras")).toHaveAttribute("aria-pressed", "true");
  await camera.getByLabel("Camera view").selectOption("coverage");
  await expect(camera.getByRole("heading", { name: "Coverage" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await checkRail();

  await activate(button(rail, "Site list"));
  await expect(camera).toHaveCount(0);
  await expect(heading(page, "Alerts")).toBeVisible();
  await expect(button(rail, "Site list")).toBeFocused();
  await checkRail();

  await activate(button(rail, "Map"));
  await expect(map).toBeVisible();
  await expect(button(rail, "Map")).toBeFocused();
  await checkRail();
});

test("WebGL startup failure and context loss keep the list usable", async ({ page }) => {
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, options?: unknown) {
      if (!sessionStorage.getItem("allow-webgl") && (type === "webgl" || type === "webgl2" || type === "experimental-webgl")) return null;
      return Reflect.apply(getContext, this, [type, options]);
    } as typeof getContext;
  });
  await page.goto("/");
  await expect(heading(page, "Alerts")).toBeFocused();
  await expect(page.getByRole("status").filter({ hasText: "Map unavailable. Site list is open." })).toHaveCount(1);
  expect(await page.evaluate((key) => localStorage.getItem(key), viewKey)).toBeNull();
  await activate(button(page, /^Motion detected/));
  await expect(heading(review(page), "Motion detected")).toBeFocused();
  await page.evaluate(() => sessionStorage.setItem("allow-webgl", "1"));
  await activate(button(page, "Map"));
  await expect(button(page.getByRole("region", { name: "Site map" }), "Fit demo site")).toBeEnabled();
  await expect(heading(review(page), "Motion detected")).toBeVisible();
  await page.locator("canvas.maplibregl-canvas").dispatchEvent("webglcontextlost");
  await expect(heading(review(page), "Motion detected")).toBeFocused();
  await expect(button(page, "Map")).toHaveAttribute("title", "Retry map");
  expect(await page.evaluate((key) => localStorage.getItem(key), viewKey)).toBe("map");
});

test("incident replay, investigation, and telemetry stay usable without the map", async ({ page, baseURL }) => {
  await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
  await page.route("**/test-gate-recording.mp4", (route) => route.fulfill({
    path: fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url)), contentType: "video/mp4",
  }));
  await page.goto("/");
  const base = Date.now();
  const event = (id: string, sensorId: string, seconds: number, summary: string) => ({
    id, sensorId, occurredAt: new Date(base + seconds * 1000).toISOString(), summary,
  });
  const door = { ...event("door", "gate-01", 0, "Gate held open"), recording: {
    cameraId: "cam-02", format: "mp4", url: baseURL + "/test-gate-recording.mp4", eventOffsetSeconds: 1, durationSeconds: 6,
  } };
  const camera = { ...event("vision", "cam-02", 1, "Camera sighting"), position: [-122.3014, 37.7884] };
  const rf = event("rf", "rf-01", 2, "Unlocated RF detection");
  await importJSON(page, { type: "telemetry", observations: [rf, camera, door] });
  const alertRow = button(page.getByRole("region", { name: "Alerts" }), /^Gate held open/);
  await expect(alertRow).toContainText("Medium priority");
  await activate(alertRow);
  const panel = review(page);
  await panel.getByLabel("Operator name").fill("Shift lead");
  await activate(button(panel, "Take ownership"));
  await expect(heading(panel, "Investigation")).toBeFocused();
  await panel.getByLabel("Investigation note").fill("Contacted site team.");
  const rail = sections(page);
  await activate(button(rail, "Cameras"));
  await expect(panel).toHaveCount(0);
  await activate(button(rail, "Site list"));
  await expect(panel.getByLabel("Investigation note")).toHaveValue("Contacted site team.");
  await button(panel, "Add note").click();
  await expect(panel.getByRole("list", { name: "Incident activity" })).toContainText("Contacted site team.");

  await activate(button(panel, "Replay timeline"));
  await expect(heading(panel, "Incident replay")).toBeFocused();
  const timeline = panel.getByRole("list", { name: "Replay timeline" });
  await expect(timeline.locator("strong")).toHaveText(["RF energy detected", "Gate held open", "Camera sighting", "Unlocated RF detection"]);
  await activate(button(panel, "Play from event"));
  const video = panel.locator("video");
  await expect.poll(() => video.evaluate((node) => node.currentTime)).toBeGreaterThanOrEqual(1);
  await video.evaluate((node) => node.pause());
  const next = button(panel, "Next event");
  await next.focus();
  await importJSON(page, event("late", "rf-01", 3, "Later RF detection"));
  await expect(button(timeline, /Later RF detection/)).toBeVisible();
  await expect(next).toBeFocused();
  await expect(video).toBeVisible();
  await activate(button(timeline, /Camera sighting/));
  await expect(heading(panel.getByRole("region", { name: "Selected replay event" }), "Camera sighting")).toBeFocused();
  await expect(panel.getByText(/Latitude 37.78840°/)).toBeVisible();
  await activate(button(timeline, /Unlocated RF detection/));
  await expect(panel.getByText("Location unavailable")).toBeVisible();
  await expect(panel.getByText("No recording for this event")).toBeVisible();
  await checkAxe(page);
  await activate(button(panel, "Back to incident"));
  await expect(button(panel, "Replay timeline")).toBeFocused();
  await panel.getByLabel("Disposition").selectOption("confirmed");
  await activate(button(panel, "Resolve alert"));
  await expect(heading(panel, "Investigation")).toBeFocused();
  await activate(button(panel, "Back to alerts"));
  await expect(alertRow).toBeFocused();
  await expect(alertRow).toContainText("Resolved");
});

test("incident queue groups explainable events, opens a clip, and restores a saved handoff search", async ({ page, baseURL }) => {
  await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
  await page.route("**/queue-clip.mp4", (route) => route.fulfill({
    path: fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url)), contentType: "video/mp4",
  }));
  await page.goto("/");
  await page.evaluate(() => {
    const key = "proxigo.global-view.preview.v1";
    const data = JSON.parse(localStorage.getItem(key)!);
    data.boundaries.find((boundary: { id: string }) => boundary.id === "east-access").sensorIds.push("cam-02");
    localStorage.setItem(key, JSON.stringify(data));
  });
  await page.reload();
  const queue = page.getByRole("region", { name: "Alerts", exact: true });
  await queue.locator("summary", { hasText: "Door and camera correlation" }).click();
  await queue.getByLabel("Group new matching events").check();
  await button(queue, "Save correlation rule").click();
  const now = Date.now() - 60_000;
  const door = { id: "queue-door", sensorId: "gate-01", occurredAt: new Date(now).toISOString(), eventType: "door-forced-open", summary: "Forced gate open" };
  const crossing = { id: "queue-crossing", sensorId: "cam-02", occurredAt: new Date(now + 5000).toISOString(), eventType: "line-crossing", summary: "Perimeter crossing",
    recording: { format: "mp4", cameraId: "cam-02", url: baseURL + "/queue-clip.mp4", eventOffsetSeconds: 1, durationSeconds: 6 } };
  await importJSON(page, { type: "telemetry", observations: [door, crossing] });
  await expect(button(queue, /^Forced door · camera corroboration/)).toBeVisible();
  await button(queue, "Preview on history").click();
  await expect(queue.getByText(/1 matching pair within 45 seconds/)).toBeVisible();
  await queue.getByLabel("Search incidents").fill("Forced gate");
  await expect(queue.getByText("1 matching incident", { exact: true })).toBeVisible();
  await button(queue, /^Forced door · camera corroboration/).click();
  const panel = review(page);
  await expect(panel.getByRole("region", { name: "Correlated source events" })).toContainText("5.0 seconds apart");
  await panel.getByLabel("Operator name").fill("Morning operator");
  await button(panel, "Take ownership").click();
  await panel.locator("summary", { hasText: "Response checklist and handoff" }).click();
  await panel.getByLabel("Next action").fill("Inspect gate hinge");
  await panel.getByLabel("Due (UTC)").fill(new Date(now - 60_000).toISOString().slice(0, 16));
  await button(panel, "Save handoff").click();
  await button(panel, "Back to alerts").click();
  await button(queue, "Handoffs").click();
  await expect(button(queue, /^Forced door · camera corroboration/)).toContainText("Overdue");
  await queue.locator("summary", { hasText: "History filters and saved searches" }).click();
  await queue.getByLabel("Event type").selectOption("line-crossing");
  await queue.getByRole("combobox", { name: "Footage", exact: true }).selectOption("recording");
  await queue.getByLabel("Search name").fill("Overdue gate handoffs");
  await button(queue, "Save incident search").click();
  await page.reload();
  await queue.locator("summary", { hasText: "History filters and saved searches" }).click();
  await queue.getByLabel("Saved incident search").selectOption("Overdue gate handoffs");
  await expect(queue.getByText("1 matching incident", { exact: true })).toBeVisible();
  await button(queue, "Open event clip").click();
  await expect(panel.getByRole("region", { name: "Selected replay event" }).getByRole("heading", { name: "Perimeter crossing" })).toBeVisible();
  await expect(panel.getByRole("list", { name: "Replay timeline" })).toContainText("Forced gate open");
  await expect.poll(() => panel.locator("video").evaluate((video) => video.currentTime)).toBeGreaterThanOrEqual(1);
});

test("a floor plan renders on the map and scopes the keyboard site list", async ({ page, baseURL }) => {
  await page.route("**/test-floor.png", (route) => route.fulfill({ contentType: "image/png", body: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVgAAAABJRU5ErkJggg==", "base64") }));
  await page.goto("/");
  const rail = sections(page);
  await button(rail, "Map layers").click();
  const inventory = page.getByRole("complementary", { name: "Map layers and site inventory" });
  await inventory.locator("summary", { hasText: "Buildings and floor plans" }).click();
  await inventory.getByLabel("Building name").fill("Dispatch center");
  await inventory.getByLabel("Floor name", { exact: true }).fill("Ground level");
  await inventory.getByLabel("Floor plan image URL").fill(baseURL + "/test-floor.png");
  await inventory.getByLabel("Northwest corner (longitude, latitude)").fill("-122.307, 37.792");
  await inventory.getByLabel("Southeast corner (longitude, latitude)").fill("-122.306, 37.790");
  const raster = page.waitForResponse((response) => response.url().endsWith("/test-floor.png") && response.ok());
  await button(inventory, "Save floor plan").click();
  await raster;
  await button(rail, "Site list").click();
  await button(rail, "Devices").click();
  const list = page.getByRole("region", { name: "Devices", exact: true });
  await expect(list.getByLabel("Site level").locator("option:checked")).toHaveText("Ground level");
  await list.getByLabel("Site level").selectOption("");
  await button(list, /North perimeter camera/).click();
  await button(review(page), "Edit device").click();
  await review(page).getByLabel("Site level").selectOption({ label: "Ground level" });
  await button(review(page), "Save device").click();
  await button(review(page), "Back to list").click();
  await expect(button(list, /North perimeter camera/)).toBeVisible();
  await expect(button(list, /Yard RF receiver/)).toHaveCount(0);
});

test("site insights filter boundaries and drill through without losing the operator's place", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
  await page.goto("/");
  await activate(button(sections(page), "Posture"));
  const insights = page.getByRole("region", { name: "Site insights" });
  await expect(insights).toContainText("Local preview with simulated data");

  const incidents = button(insights, /Open incidents$/);
  const checks = button(insights, /Checks due$/);
  const coverage = button(insights, /Coverage at risk$/);
  await activate(incidents);
  await expect(incidents).toHaveAttribute("aria-pressed", "true");
  await expect(button(insights, "Inspect East access road")).toHaveCount(0);
  await activate(coverage);
  await expect(coverage).toHaveAttribute("aria-pressed", "true");
  await expect(button(insights, "Inspect East access road")).toBeVisible();
  await activate(checks);
  await expect(checks).toHaveAttribute("aria-pressed", "true");
  await activate(button(insights, "Inspect Restricted yard"));
  const readiness = insights.getByRole("region", { name: "Readiness for Restricted yard" });
  await expect(readiness).toContainText("Field of view unverified");
  await expect(readiness).toContainText("Available on published sites");
  const camera = button(readiness, "Loading dock camera");
  await activate(camera);
  const panel = review(page);
  await expect(heading(panel, "Loading dock camera")).toBeFocused();
  await activate(button(panel, "Back to list"));
  await expect(camera).toBeFocused();
  await activate(button(readiness, "Open boundary"));
  await expect(heading(panel, "Restricted yard")).toBeFocused();
  await activate(button(panel, "Back to list"));
  const incident = readiness.getByRole("list", { name: "Open boundary incidents" }).getByRole("button").first();
  await activate(incident);
  await expect(button(panel, "Replay timeline")).toBeVisible();
  await activate(button(panel, "Back to Posture"));
  await expect(incident).toBeFocused();
  await activate(button(insights, "Clear insights filter"));
  await expect(checks).toHaveAttribute("aria-pressed", "false");
  await expect(button(insights, "Inspect East access road")).toBeVisible();
  await expect.poll(() => page.locator(".gv-site-list").evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);

  await page.setViewportSize({ width: 1440, height: 950 });
  await button(sections(page), "Map").click();
  await button(sections(page), "Posture").click();
  await activate(incidents);
  await activate(button(insights, "Inspect Restricted yard"));
  await activate(incident);
  await expect(button(panel, "Back to Posture")).toBeVisible();
  await activate(button(panel, "Back to Posture"));
  await expect(incident).toBeFocused();
  await expect(incidents).toHaveAttribute("aria-pressed", "true");
});

test("health triage and maintenance reflow at 320 CSS pixels", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
  await page.goto("/");
  await activate(button(sections(page), "Posture"));
  await expect(heading(page, "Posture")).toBeFocused();
  const offline = button(page.getByRole("list", { name: "Appliance health summary" }), /^Offline/);
  await activate(offline);
  const affected = page.getByRole("region", { name: "Affected devices and boundaries" });
  const gate = button(affected, /^East gate contact/);
  expect((await gate.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await expect(button(affected, /^East access road/)).toBeVisible();
  await activate(gate);
  const panel = review(page);
  await expect(heading(panel, "East gate contact")).toBeFocused();
  await expect(heading(page, "Posture")).toHaveCount(0);
  await activate(button(panel, "Back to list"));
  await expect(gate).toBeFocused();
  await activate(page.locator("summary").filter({ hasText: /^Outage history/ }));
  await expect(page.getByText("No recorded outages.")).toBeVisible();
  await activate(page.locator("summary").filter({ hasText: /^Maintenance/ }));
  const schedule = button(page, "Schedule maintenance");
  await activate(schedule);
  const devicePicker = page.getByLabel("Device", { exact: true });
  await expect(devicePicker).toBeFocused();
  await expect(devicePicker).toHaveValue("gate-01");
  await page.getByLabel("Reason").fill("Gate power inspection");
  await activate(button(page, "Save maintenance"));
  await expect(schedule).toBeFocused();
  await expect(page.getByText(/Scheduled · Gate power inspection|Active · Gate power inspection/)).toBeVisible();
  await expect.poll(() => page.locator(".gv-site-list").evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await expect.poll(() => page.evaluate(() => matchMedia("(forced-colors: active)").matches)).toBe(true);
  await expect(offline).toHaveCSS("outline-style", "solid");
  await page.emulateMedia({ forcedColors: "none", reducedMotion: "reduce" });
  await expect(page.locator(".gv-app")).toHaveCSS("color", "rgb(250, 250, 250)");
  await checkAxe(page);
  await activate(button(affected, "Clear health filter"));
  await expect(offline).toBeFocused();

  await activate(button(sections(page), "Devices"));
  const cameraRow = button(page, /^North perimeter camera/);
  await activate(cameraRow);
  await expect(heading(panel, "North perimeter camera")).toBeFocused();
  const edit = button(panel, "Edit device");
  await activate(edit);
  await expect(heading(panel, "Edit device")).toBeFocused();
  const deviceType = panel.getByRole("combobox", { name: "Device type" });
  await deviceType.selectOption("rf");
  await expect(deviceType).toHaveValue("rf");
  await checkAxe(page);
  await activate(button(panel, "Cancel"));
  await expect(edit).toBeFocused();
  await activate(button(panel, "Back to list"));
  await expect(cameraRow).toBeFocused();

  await activate(button(sections(page), "Boundaries"));
  await expect(heading(page, "Boundaries")).toBeFocused();
  const boundary = button(page, /^East access road/);
  await activate(boundary);
  await expect(heading(panel, "East access road")).toBeFocused();
  const editBoundary = button(panel, "Edit boundary");
  await activate(editBoundary);
  await expect(heading(panel, "Edit boundary")).toBeFocused();
  await expect(panel.getByText("Switch to Map to edit the boundary shape.")).toBeVisible();
  await activate(button(panel, "Cancel"));
  await expect(editBoundary).toBeFocused();
  await activate(button(panel, "Back to list"));
  await expect(boundary).toBeFocused();
});

test("two recorded angles seek and play on the same event clock", async ({ page, baseURL }) => {
  await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
  const fixture = await readFile(fileURLToPath(new URL("./fixtures/gate-recording.mp4", import.meta.url)));
  await page.route("**/comparison.mp4", async (route) => {
    const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
    if (!range) return route.fulfill({ body: fixture, contentType: "video/mp4", headers: { "Accept-Ranges": "bytes" } });
    const start = Number(range[1]), end = range[2] ? Math.min(Number(range[2]), fixture.length - 1) : fixture.length - 1;
    return route.fulfill({ status: 206, body: fixture.subarray(start, end + 1), contentType: "video/mp4",
      headers: { "Accept-Ranges": "bytes", "Content-Range": `bytes ${start}-${end}/${fixture.length}` } });
  });
  await page.goto("/");
  await page.evaluate(() => {
    const key = "proxigo.global-view.preview.v1";
    const data = JSON.parse(localStorage.getItem(key)!);
    data.boundaries.find((area: { id: string }) => area.id === "restricted-yard").sensorIds.push("cam-01");
    localStorage.setItem(key, JSON.stringify(data));
  });
  await page.reload();
  const eventTime = Date.now() - 10_000;
  const clip = { format: "mp4", url: baseURL + "/comparison.mp4", eventOffsetSeconds: 2, durationSeconds: 6 };
  await importJSON(page, { type: "telemetry", observations: [
    { id: "angle-first", sensorId: "cam-02", summary: "Primary camera angle", position: [-122.3014, 37.7884],
      occurredAt: new Date(eventTime).toISOString(), recording: { ...clip, cameraId: "cam-02" } },
    { id: "angle-second", sensorId: "cam-01", summary: "Secondary camera angle", position: [-122.3014, 37.7884],
      occurredAt: new Date(eventTime + 1000).toISOString(), recording: { ...clip, cameraId: "cam-01" } },
  ] });
  await activate(button(page, /^Primary camera angle/));
  const panel = review(page);
  await activate(button(panel, "Replay timeline"));
  await activate(button(panel, "Compare camera angles"));
  const videos = panel.getByRole("region", { name: "Synchronized recordings" }).locator("video");
  await expect.poll(() => videos.evaluateAll((nodes: HTMLVideoElement[]) => nodes.map((node) => node.currentTime))).toEqual([2, 1]);
  await activate(button(panel, "Play both"));
  await expect.poll(() => videos.first().evaluate((node) => node.currentTime)).toBeGreaterThan(2.25);
  await activate(button(panel, "Pause both"));
  expect(await videos.evaluateAll((nodes: HTMLVideoElement[]) => nodes.every((node) => node.paused))).toBe(true);
  await activate(button(panel, "Seek to event"));
  await expect.poll(() => videos.evaluateAll((nodes: HTMLVideoElement[]) => nodes.map((node) => node.currentTime))).toEqual([2, 1]);
});

test("the operator can review and replay an incident at 200% Chrome zoom", async ({ baseURL }) => {
  const profile = await mkdtemp(join(tmpdir(), "proxigo-chrome-zoom-"));
  try {
    await mkdir(join(profile, "Default"));
    await writeFile(join(profile, "Default", "Preferences"), JSON.stringify({
      partition: { default_zoom_level: { x: Math.log(2) / Math.log(1.2) } },
    }));
    const context = await chromium.launchPersistentContext(profile, {
      channel: "chrome", viewport: null, baseURL, args: ["--window-size=1280,1000", "--force-device-scale-factor=1"],
    });
    try {
      const page = context.pages()[0];
      await page.addInitScript((key) => localStorage.setItem(key, "list"), viewKey);
      await page.goto("/");
      await expect.poll(() => page.evaluate(() => [innerWidth, devicePixelRatio])).toEqual([640, 2]);
      const row = button(page, /^Motion detected/);
      await activate(row);
      const panel = review(page);
      await expect(heading(panel, "Motion detected")).toBeFocused();
      await expect(heading(page, "Alerts")).toHaveCount(0);
      await activate(button(panel, "Replay timeline"));
      await expect(heading(panel, "Incident replay")).toBeFocused();
      expect(await panel.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth)).toBe(true);
      await activate(button(panel, "Back to incident"));
      await expect(button(panel, "Replay timeline")).toBeFocused();
      await activate(button(panel, "Back to alerts"));
      await expect(row).toBeFocused();
    } finally { await context.close(); }
  } finally { await rm(profile, { recursive: true, force: true }); }
});
