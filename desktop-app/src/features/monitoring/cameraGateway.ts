import { httpEndpointUrl, type CameraGatewayKind } from "./monitoringModel";

export interface CameraGatewayDraft {
  gateway: string;
  token: string;
  address: string;
  username: string;
  password: string;
  profile: string;
  gridProfile: string;
  gridAddress: string;
}

export interface OnvifProfile { token: string; name: string }

export const CAMERA_GATEWAY_DEFAULTS: CameraGatewayDraft = {
  gateway: "http://127.0.0.1:8765", token: "", address: "", username: "", password: "", profile: "", gridProfile: "", gridAddress: "",
};

function gatewayBase(value: string) {
  const endpoint = httpEndpointUrl(value);
  if (!endpoint) return null;
  const url = new URL(endpoint);
  if (url.hash || url.search) return null;
  return url.href.endsWith("/") ? url.href : url.href + "/";
}

export function gatewayDraftReady(draft: CameraGatewayDraft, type: CameraGatewayKind) {
  if (!gatewayBase(draft.gateway) || !draft.token.trim() || (!draft.username && draft.password)) return false;
  try {
    const address = new URL(draft.address.trim());
    if (type === "rtsp" && draft.gridAddress.trim()) {
      const grid = new URL(draft.gridAddress.trim());
      if (grid.protocol !== "rtsp:" || grid.hostname !== address.hostname || grid.username || grid.password || grid.hash || grid.href === address.href) return false;
    }
    return (type === "rtsp" ? address.protocol === "rtsp:" : ["http:", "https:"].includes(address.protocol))
      && !!address.hostname && !address.username && !address.password && !address.hash;
  } catch { return false; }
}

async function requestGateway(draft: CameraGatewayDraft, type: CameraGatewayKind, endpoint: "profiles" | "cameras",
  signal: AbortSignal, deviceId?: string) {
  const base = gatewayBase(draft.gateway);
  if (!base || !gatewayDraftReady(draft, type)) throw new Error("Enter the gateway URL, token, and camera address.");
  if (location.protocol === "https:" && base.startsWith("http:") && !["localhost", "127.0.0.1", "[::1]"].includes(new URL(base).hostname)) {
    throw new Error("Use an HTTPS gateway for a remote camera gateway in this workspace.");
  }
  const response = await fetch(new URL("api/" + endpoint, base), {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + draft.token.trim() },
    body: JSON.stringify({ type, address: draft.address.trim(), username: draft.username, password: draft.password, profile: draft.profile,
      gridProfile: draft.gridProfile, gridAddress: draft.gridAddress.trim(), deviceId }),
    credentials: "omit",
    redirect: "error",
    signal,
  }).catch(() => {
    throw new Error(signal.aborted ? "The camera gateway request timed out." : "Could not reach the camera gateway. Check its address and browser access settings.");
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof result?.error === "string" ? result.error : `The gateway returned HTTP ${response.status}.`);
  return { base, result };
}

export async function discoverCameraProfiles(draft: CameraGatewayDraft, signal: AbortSignal): Promise<OnvifProfile[]> {
  const { result } = await requestGateway(draft, "onvif", "profiles", signal);
  if (!Array.isArray(result?.profiles) || !result.profiles.length || !result.profiles.every((profile: unknown) =>
    profile && typeof profile === "object" && "token" in profile && typeof profile.token === "string" && "name" in profile && typeof profile.name === "string")) {
    throw new Error("The gateway returned no usable ONVIF profiles.");
  }
  return result.profiles;
}

export async function connectGatewayCamera(draft: CameraGatewayDraft, type: CameraGatewayKind, deviceId: string, signal: AbortSignal) {
  const { base, result } = await requestGateway(draft, type, "cameras", signal, deviceId);
  if (typeof result?.streamPath !== "string" || !/^streams\/[0-9a-f]{48}\/index\.m3u8$/.test(result.streamPath)) {
    throw new Error("The gateway returned an invalid live stream path.");
  }
  const gridPath = result.streamPath.replace(/index\.m3u8$/, "grid/index.m3u8");
  if (result.gridStreamPath !== undefined && result.gridStreamPath !== gridPath) throw new Error("The gateway returned an invalid wall stream path.");
  if ((type === "rtsp" ? draft.gridAddress.trim() : draft.gridProfile) && result.gridStreamPath !== gridPath) {
    throw new Error("The gateway did not return the requested wall stream. Update the gateway and reconnect.");
  }
  return { cameraFeedUrl: new URL(result.streamPath, base).href, cameraGridUrl: result.gridStreamPath ? new URL(gridPath, base).href : "" };
}
