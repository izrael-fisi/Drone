import type { Sensor } from "./monitoringModel";

export function gatewayStream(camera?: Sensor) {
  if (!camera?.cameraGatewayKind || !camera.cameraFeedUrl) return null;
  try {
    const url = new URL(camera.cameraFeedUrl);
    const match = url.pathname.match(/^(.*)\/streams\/([a-f0-9]{48})\/index\.m3u8$/);
    return match ? { base: new URL(match[1] + "/", url.origin).href, key: match[2], feed: url.href } : null;
  } catch { return null; }
}
