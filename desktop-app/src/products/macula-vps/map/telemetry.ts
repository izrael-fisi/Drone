import { useEffect } from "react";
import { useShellStore } from "../../../lib/shellStore";
import { useAppStore } from "../../../lib/store";
import { cmd } from "../../../lib/tauri";
import { positionLatLon } from "./helpers";
import { useVpsMapStore } from "./store";

/**
 * Poll the active VPS device's position (UDP status bridge, then edge MAVLink)
 * every 1.6 s while Macula VPS is licensed, whichever product is in view, so
 * switching products mid-flight loses no telemetry.
 */
export function useVpsTelemetry(entitled: boolean) {
  const activeDevice = useAppStore((state) => state.devices.find((device) => device.id === state.activeDeviceId));
  const setLivePosition = useShellStore((state) => state.setLivePosition);
  const host = activeDevice?.host;
  const endpoint = activeDevice?.mavlink_endpoint;
  const autopilot = activeDevice?.autopilot;

  useEffect(() => {
    if (!entitled) return;
    const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
    const setTelemetryMessage = (telemetryMessage: string) => useVpsMapStore.setState({ telemetryMessage });
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        let packet = await cmd.receivePositionUpdate(positionPort, 300);
        if (!packet && host && endpoint) {
          const edgePacket = await cmd.edgeApiMavlinkPosition(`http://${host}:5000`, endpoint, 1.2, autopilot);
          if (edgePacket.ok && positionLatLon(edgePacket)) {
            packet = edgePacket;
          } else if (!cancelled) {
            setTelemetryMessage(edgePacket.message ?? "MAVLink position waiting");
          }
        }
        if (cancelled) return;
        if (packet) {
          useVpsMapStore.setState({ position: packet });
          setLivePosition(packet);
          const mavlinkMessage = (packet as { mavlink?: { message_type?: string } }).mavlink?.message_type ?? null;
          setTelemetryMessage(mavlinkMessage ? `MAVLink ${mavlinkMessage}` : `packet ${packet.sequence ?? "n/a"}`);
        } else {
          setTelemetryMessage("waiting");
        }
      } catch (error) {
        if (!cancelled) setTelemetryMessage(`unavailable: ${String(error)}`);
      } finally {
        if (!cancelled) timer = setTimeout(poll, 1600);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [autopilot, endpoint, entitled, host, setLivePosition]);
}
