import type { BottomDockTabId } from "../../../lib/shellStore";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { PanelAction, PanelStack, SectionLabel, SettingLine, StateCard, StatusPill } from "../../../platform/ui";

export function DiagnosticsSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const connected = model.edgeConnectionState === "online";
  const host = model.activeDevice?.host;

  return (
    <PanelStack title="Diagnostics Settings" subtitle="Remote support and service evidence">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="Edge API" value={connected ? "Live" : "Offline"} detail={host ?? "no host"} tone={connected ? "green" : "muted"} />
        <StateCard label="Runtime" value={model.activeDevice?.remote_project_path ? "Path Set" : "No Path"} detail="service root" tone={model.activeDevice?.remote_project_path ? "orange" : "muted"} />
      </div>
      <SectionLabel>Support</SectionLabel>
      <SettingLine label="Remote support" detail={host ? `Device IP ${host}` : "Device unavailable"}>
        <StatusPill tone={connected ? "warning" : "muted"}>{connected ? "Stopped" : "Offline"}</StatusPill>
      </SettingLine>
      <SettingLine label="Lockdown diagnostics" detail="Security scan and cleanup">
        <StatusPill tone="muted">Not run</StatusPill>
      </SettingLine>
      <PanelAction label="Diagnostics Dock" detail="Open bottom diagnostics output" onClick={() => setBottomDockTab("diagnostics")} />
      <PanelAction label="Enable Remote Support" detail="Requires connected edge runtime" disabled />
      <PanelAction label="Delete All Logs" detail="Disabled until lockdown scan completes" disabled />
    </PanelStack>
  );
}
