import { ChevronRight, Pentagon } from "lucide-react";
import { BOUNDARY_VISIBILITY, type BoundaryPosture } from "./monitoringModel";
import { HealthTriage, type HealthTriageProps } from "./HealthTriage";
import { coveragePercent } from "./cameraCoverage";

export function SitePosture({ areas, triage, insights, video, cameraGaps, selected, onSelect }: {
  insights?: import("react").ReactNode; video?: import("react").ReactNode;
  triage: HealthTriageProps;
  areas: BoundaryPosture[]; cameraGaps?: Map<string, number>; selected?: string; onSelect: (id: string) => void;
}) {
  const order = ["unassigned", "no-live-detections", "paused"] as const;
  const counts = areas.reduce((totals, area) => ({ ...totals, [area.visibility]: totals[area.visibility] + 1 }), { unassigned: 0, "no-live-detections": 0, paused: 0 });
  return <div className="gv-posture">
    {!triage.filter && insights}
    <HealthTriage {...triage} />
    {!triage.filter && <>
    {video}
    <div className="gv-posture-metrics" aria-label="Boundary visibility summary">
      {order.map((status) => <div key={status}><strong style={{ color: BOUNDARY_VISIBILITY[status].color }}>{counts[status]}</strong><span>{BOUNDARY_VISIBILITY[status].label}</span></div>)}
    </div>
    <p className="gv-posture-note">{cameraGaps
      ? "Camera gaps use configured bearings, angles, ranges, and marked obstructions."
      : "Camera gap calculation is unavailable. Boundary assignments and detection status are shown below."}</p>
    <div className="gv-posture-areas">
      {order.flatMap((status) => areas.filter((area) => area.visibility === status)).map(({ boundary, assignedCount, visibility }) => {
        const gap = cameraGaps?.get(boundary.id);
        return (
        <button className="gv-posture-row" key={boundary.id} type="button" data-focus-key={"boundary" + boundary.id}
          aria-pressed={selected === boundary.id} onClick={() => onSelect(boundary.id)}>
          <Pentagon size={17} style={{ color: BOUNDARY_VISIBILITY[visibility].color }} />
          <span><strong>{boundary.name}</strong><small>{BOUNDARY_VISIBILITY[visibility].label} · {assignedCount} {assignedCount === 1 ? "device" : "devices"}</small>
            {gap !== undefined && <small className={gap > 0 ? "gv-camera-gap-text" : undefined}>
              {gap === 0 ? "No modeled camera gap" : `${coveragePercent(gap)} camera gap`}
            </small>}
          </span>
          <ChevronRight size={14} />
        </button>
        );
      })}
      {!areas.length && <p className="gv-empty">Draw a boundary to assess its visibility.</p>}
    </div>
    </>}
  </div>;
}
