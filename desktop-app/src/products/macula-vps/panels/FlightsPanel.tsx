import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { PanelAction, PanelStack, StateCard, StatusRow } from "../../../platform/ui";

export function FlightsPanel({ model }: { model: OperatorRuntimeModel }) {
  return (
    <PanelStack title="Flights" subtitle="Logs and playback">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="On Device" value={model.activeDevice ? "Ready" : "Offline"} detail="requires runtime sync" tone={model.activeDevice ? "green" : "muted"} />
        <StateCard label="Local" value={model.flights.length} detail="indexed records" tone={model.flights.length ? "orange" : "muted"} />
      </div>
      <PanelAction label="Sync Recordings" detail="Requires edge recording service" disabled />
      <StatusRow label="GPS vs vision" value="Read from support bundle summaries" healthy={false} />
      <StatusRow label="Notes" value="Stored with local flight records when present" healthy={false} />
      <StatusRow label="MCAP export" value="Shown when bundle evidence exists" healthy={false} />
    </PanelStack>
  );
}
