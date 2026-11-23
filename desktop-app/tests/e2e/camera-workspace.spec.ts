import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const cameras = (page: Page) => page.getByRole("region", { name: "Cameras", exact: true });
const fixturePath = (path: string) => fileURLToPath(new URL("./fixtures/" + path, import.meta.url));
const launcher = (page: Page) => page.getByRole("button", { name: "Cameras", exact: true });
const review = (page: Page) => page.getByRole("complementary", { name: "Monitor and review" });

test("configure and verify both streams, then use a saved live wall", async ({ page, baseURL }) => {
  await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
  await page.route(/\/(primary|grid)\/live\.m3u8$/, (route) => route.fulfill({
    path: fixturePath("camera-tile.m3u8"), contentType: "application/vnd.apple.mpegurl",
  }));
  await page.route("**/camera-tile-0.ts", (route) => route.fulfill({
    path: fixturePath("camera-tile-0.ts"), contentType: "video/mp2t",
  }));
  await page.goto("/");
  await launcher(page).press("Enter");
  const hub = cameras(page);
  await expect(hub.getByRole("heading", { name: "Live wall", exact: true })).toBeFocused();
  await expect(hub.getByRole("article")).toHaveCount(2);
  await hub.getByLabel("Camera view").selectOption("directory");
  const north = "North perimeter camera";
  await hub.getByRole("button", { name: "Edit " + north }).click();
  const form = review(page);
  await expect(form.getByRole("heading", { name: "Edit device" })).toBeFocused();
  const primary = form.getByRole("textbox", { name: "HLS feed URL", exact: true });
  const grid = form.getByRole("textbox", { name: "Lower-bandwidth HLS URL", exact: true });
  await primary.fill(baseURL + "/primary/live.m3u8");
  await grid.fill("rtsp://site.example/camera");
  await expect(grid).toHaveAttribute("aria-invalid", "true");
  await expect(form.getByRole("button", { name: "Save device" })).toBeDisabled();
  await grid.fill(baseURL + "/grid/live.m3u8");
  await form.getByLabel("Video system").fill("Harbor VMS");
  await form.getByLabel("Channel reference").fill("Recorder A / 7");
  await form.getByRole("button", { name: "Test camera feed", exact: true }).click();
  await expect(form.locator(".gv-feed-status")).toContainText("Playing");
  const substreamTest = page.waitForRequest("**/grid/live.m3u8");
  await form.getByRole("button", { name: "Test lower-bandwidth feed" }).click();
  await substreamTest;
  await expect(form.locator(".gv-feed-status")).toContainText("Playing");
  await form.getByRole("button", { name: "Save device" }).click();
  await expect(form.getByRole("heading", { name: north })).toBeVisible();

  const wallRequest = page.waitForRequest("**/grid/live.m3u8");
  await launcher(page).click();
  await wallRequest;
  const tile = hub.getByRole("article", { name: north });
  await expect(tile).toContainText("Lower-bandwidth feed");
  await expect(hub.locator(".gv-feed-status").getByText("Playing", { exact: true })).toBeVisible();
  await expect(form.locator("video")).toHaveCount(0);
  await hub.getByRole("button", { name: "Pause wall" }).click();
  await expect(hub.locator("video")).toHaveCount(0);
  await expect(tile).toContainText("Wall paused");
  await hub.getByRole("button", { name: "Pin " + north }).click();
  await hub.getByLabel("Wall view").selectOption("pinned");
  await expect(hub.getByRole("article")).toHaveCount(1);
  await hub.getByLabel("Cameras per page").selectOption("1");
  await hub.getByRole("button", { name: "Save wall as" }).click();
  await hub.getByLabel("Wall name").fill("North watch");
  await hub.getByRole("button", { name: "Save wall", exact: true }).click();
  await expect(hub.getByLabel("Wall view")).toHaveValue("saved:north watch");

  const focus = hub.getByRole("button", { name: "Focus " + north });
  const primaryRequest = page.waitForRequest("**/primary/live.m3u8");
  await focus.press("Enter");
  await primaryRequest;
  await expect(hub.getByRole("heading", { name: north })).toBeFocused();
  await expect(hub.getByRole("region", { name: "Camera configuration" })).toContainText("Recorder A / 7");
  const qualityRequest = page.waitForRequest("**/grid/live.m3u8");
  await hub.getByLabel("Stream quality").selectOption("grid");
  await qualityRequest;
  await hub.getByRole("button", { name: "Back to cameras" }).press("Enter");
  await expect(focus).toBeFocused();
  const list = page.getByRole("button", { name: "Site list", exact: true });
  await list.press("Enter");
  await expect(hub).toHaveCount(0);
  await expect(list).toBeFocused();
  await page.reload();
  await launcher(page).click();
  await expect(hub.getByLabel("Wall view")).toHaveValue("saved:north watch");
  await expect(hub.getByLabel("Cameras per page")).toHaveValue("1");
  await expect(hub.getByRole("article")).toHaveCount(1);
  await expect(hub.getByRole("button", { name: "Pin " + north })).toHaveAttribute("aria-pressed", "true");
  await hub.getByRole("button", { name: "Remove saved wall" }).click();
  await expect(hub.getByRole("article")).toHaveCount(1);
  await hub.getByRole("button", { name: "Next", exact: true }).click();
  await expect(hub.getByText("Page 2 of 2")).toBeVisible();
  await expect(hub.getByRole("article", { name: north })).toBeVisible();

  await hub.getByRole("button", { name: "Add camera" }).click();
  await expect(form.getByRole("heading", { name: "Register device" })).toBeFocused();
  await form.getByLabel("Device name").fill("West entrance camera");
  await form.getByLabel("Latitude").fill("37.788");
  await form.getByLabel("Longitude").fill("-122.301");
  await form.getByRole("button", { name: "Save device" }).click();
  await expect(form.getByRole("heading", { name: "West entrance camera" })).toBeFocused();
  await launcher(page).click();
  await hub.getByLabel("Camera view").selectOption("directory");
  await hub.getByLabel("Camera status").selectOption("needs-feed");
  await expect(hub.getByRole("article")).toHaveText([/West entrance camera.*Needs feed.*Missing field of view/]);
});

test("camera recordings, map coverage, and compact keyboard operation", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.addInitScript(() => localStorage.setItem("proxigo.global-view.view.v1", "list"));
  await page.route("**/camera-evidence.mp4", (route) => route.fulfill({
    path: fixturePath("gate-recording.mp4"), contentType: "video/mp4",
  }));
  await page.goto("/");
  await page.getByLabel("Observation JSON file").setInputFiles({
    name: "camera-event.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({
      id: "qa-door-camera", sensorId: "gate-01", occurredAt: new Date().toISOString(), summary: "Camera evidence from the east gate",
      recording: { cameraId: "cam-02", format: "mp4", url: baseURL + "/camera-evidence.mp4", eventOffsetSeconds: 1, durationSeconds: 6 },
    })),
  });
  await launcher(page).press("Enter");
  const hub = cameras(page);
  expect(await hub.locator(".gv-camera-workspace-body").evaluate((node) => node.scrollWidth)).toBeLessThanOrEqual(
    (await hub.locator(".gv-camera-workspace-body").evaluate((node) => node.clientWidth)),
  );
  const { violations } = await new AxeBuilder({ page }).include(".gv-camera-workspace")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(violations.map(({ id, nodes }) => ({ id, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) }))).toEqual([]);

  await hub.getByLabel("Camera view").selectOption("directory");
  await hub.getByRole("button", { name: "Filters", exact: true }).press("Enter");
  await hub.getByRole("searchbox", { name: "Find cameras" }).fill("loading dock");
  const camera = hub.getByRole("button", { name: "View Loading dock camera" });
  await camera.press("Enter");
  await expect(hub.getByRole("heading", { name: "Loading dock camera" })).toBeFocused();
  await expect(hub.getByRole("region", { name: "Camera configuration" })).toContainText("East gate contact");
  const clip = hub.getByRole("region", { name: "Recent recordings" }).getByRole("button", { name: /Camera evidence from the east gate/ });
  await clip.press("Enter");
  const recording = hub.getByRole("region", { name: "Recorded video" });
  await expect(recording.getByRole("heading", { name: "Camera evidence from the east gate" })).toBeFocused();
  await expect.poll(() => recording.locator("video").evaluate((node) => node.currentTime)).toBeGreaterThanOrEqual(1);
  await hub.getByRole("button", { name: "Return to live" }).press("Enter");
  await expect(clip).toBeFocused();
  await hub.getByRole("region", { name: "Recent incidents" }).getByRole("button", { name: /Camera evidence from the east gate/ }).press("Enter");
  await expect(review(page).getByRole("heading", { name: "Camera evidence from the east gate" })).toBeFocused();

  await launcher(page).press("Enter");
  await hub.getByLabel("Camera view").selectOption("coverage");
  const north = hub.getByRole("article", { name: "North perimeter", exact: true });
  await expect(north).toContainText(/\d+% modeled gap/);
  await north.getByRole("button", { name: "Show coverage for North perimeter" }).press("Enter");
  await expect(page.getByRole("region", { name: "Site map", exact: true })).toBeVisible();
  await expect(review(page).getByRole("heading", { name: "North perimeter", exact: true })).toBeFocused();
  await expect(page.getByRole("region", { name: "Site map" }).getByText("Camera gap", { exact: true })).toBeVisible();
});
