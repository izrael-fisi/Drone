import type { BottomDockTabId } from "../../../lib/shellStore";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { PanelAction, PanelStack, StatusRow } from "../../../platform/ui";

export function MavSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const endpoint = model.activeDevice?.mavlink_endpoint;
  const connected = Boolean(endpoint);

  return (
    <PanelStack title="MAV Settings" subtitle="MAVLink routing and VPS parameters">
      <StatusRow label="Endpoint" value={endpoint ?? "not configured"} healthy={connected} />
      <StatusRow label="Primary route" value={connected ? "available" : "connect device to configure"} healthy={connected} />
      <StatusRow label="Heartbeat" value={connected ? "waiting for stream" : "offline"} healthy={false} />
      <PanelAction label="Parameters" detail="Open bottom parameters dock" onClick={() => setBottomDockTab("parameters")} />
      <PanelAction label="Messages" detail={connected ? "Open message stream" : "Requires active MAVLink endpoint"} onClick={() => setBottomDockTab("messages")} disabled={!connected} />
      <PanelAction label="Console" detail={connected ? "Open command console" : "Requires active MAVLink endpoint"} onClick={() => setBottomDockTab("console")} disabled={!connected} />
    </PanelStack>
  );
}
