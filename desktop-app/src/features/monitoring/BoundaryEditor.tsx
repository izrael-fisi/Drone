import type { FormEvent } from "react";
import { Pentagon, X } from "lucide-react";
import { chooseBoundaryTool, MAX_VERTICES, removeVertex, undoSketch, type BoundaryDraft, type BoundaryTool } from "./configurationModel";
import { BOUNDARY_TONES, parseLabels, validLabels, validateBoundary, ZONE_KINDS, type Boundary, type BoundaryTone, type Sensor, type ZoneKind, type SiteFloor } from "./monitoringModel";
import { FloorSelect } from "./SiteFloors";

const tools: { id: BoundaryTool; label: string }[] = [
  { id: "polygon", label: "Polygon" }, { id: "rectangle", label: "Rectangle" }, { id: "vertices", label: "Edit vertices" },
];

export function BoundaryForm({ editor, sensors, floors, existing, mapView, onChange, onSave, onCancel }: {
  editor: BoundaryDraft; sensors: Sensor[]; floors: SiteFloor[]; existing: boolean; mapView: boolean;
  onChange: (editor: BoundaryDraft) => void;
  onSave: (event: FormEvent, editor: BoundaryDraft) => void; onCancel: () => void;
}) {
  const { value } = editor;
  const issue = validateBoundary(value.vertices);
  const labelsValid = validLabels(parseLabels(editor.labelsText));
  const patch = (partial: Partial<Boundary>) => onChange({ ...editor, value: { ...value, ...partial } });

  return <>
    <header className="gv-panel-heading">
      <div><Pentagon size={17} /><h2>{existing ? "Edit boundary" : "New boundary"}</h2></div>
      <button className="gv-icon-button" type="button" aria-label="Cancel boundary editing" onClick={onCancel}><X size={17} /></button>
    </header>
    <form className="gv-form" onSubmit={(event) => onSave(event, editor)}>
      {mapView ? <fieldset>
        <legend>Shape</legend>
        <div className="gv-editor-tools">
          {tools.map(({ id, label }) => <button key={id} className="gv-button" type="button" aria-pressed={editor.tool === id}
            disabled={id === "vertices" && value.vertices.length < 3} onClick={() => onChange(chooseBoundaryTool(editor, id))}>{label}</button>)}
        </div>
        <p className="gv-form-geometry" role="status">{issue ?? `${value.vertices.length} vertices`}</p>
        {editor.tool === "vertices" && <div className="gv-editor-tools">
          <select aria-label="Boundary vertex" value={editor.selectedVertex ?? ""} onChange={(event) => onChange({ ...editor, selectedVertex: event.target.value ? Number(event.target.value) : null })}>
            <option value="">Select vertex</option>
            {value.vertices.map((_, index) => <option key={index} value={index}>Vertex {index + 1}</option>)}
          </select>
          <button className="gv-button" type="button" disabled={editor.selectedVertex === null || value.vertices.length <= 3} onClick={() => onChange(removeVertex(editor))}>Remove vertex</button>
        </div>}
        {editor.history.length > 0 && <button className="gv-button gv-inline-action" type="button" onClick={() => onChange(undoSketch(editor))}>Undo shape change</button>}
        {value.vertices.length >= MAX_VERTICES && <p className="gv-form-geometry">Maximum {MAX_VERTICES} vertices.</p>}
      </fieldset> : <p className="gv-form-geometry">Switch to Map to edit the boundary shape.</p>}

      <label>Boundary name<input required maxLength={60} value={value.name} onChange={(event) => patch({ name: event.target.value })} placeholder="e.g. West loading area" /></label>
      <FloorSelect floors={floors} value={value.floorId} onChange={(id) => patch({ floorId: id || undefined,
        sensorIds: value.sensorIds.filter((sensorId) => sensors.find((sensor) => sensor.id === sensorId)?.floorId === (id || undefined)) })} empty="Outdoors" />
      <label><span id="gv-boundary-type-label">Boundary type</span><select aria-labelledby="gv-boundary-type-label" value={value.kind} onChange={(event) => patch({ kind: event.target.value as ZoneKind })}>{ZONE_KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select></label>
      <details className="gv-editor-disclosure">
        <summary>Labels and styling</summary>
        <label>Map label<input maxLength={32} value={value.mapLabel ?? ""} onChange={(event) => patch({ mapLabel: event.target.value })} placeholder="Defaults to boundary name" /></label>
        <label>Labels<input maxLength={272} value={editor.labelsText} aria-invalid={!labelsValid} onChange={(event) => onChange({ ...editor, labelsText: event.target.value })} placeholder="Loading dock, After hours" /></label>
        <small>{labelsValid ? "Separate labels with commas." : "Use up to eight labels with 32 characters each."}</small>
        <label><span id="gv-map-color-label">Map color</span><select aria-labelledby="gv-map-color-label" value={value.mapTone ?? ""} onChange={(event) => patch({ mapTone: (event.target.value || undefined) as BoundaryTone | undefined })}>
          <option value="">By boundary type</option>
          {Object.entries(BOUNDARY_TONES).map(([key, tone]) => <option key={key} value={key}>{tone.label}</option>)}
        </select></label>
        <label>Notes<textarea maxLength={280} rows={2} value={value.notes ?? ""} onChange={(event) => patch({ notes: event.target.value })} /></label>
        <label>Response team<input maxLength={60} value={value.responseTeam ?? ""} onChange={(event) => patch({ responseTeam: event.target.value })} /></label>
      </details>

      {!!sensors.length && <fieldset><legend>Assigned devices</legend>{sensors.filter((sensor) => sensor.floorId === value.floorId).map((sensor) => <label key={sensor.id} className="gv-checkbox">
        <input type="checkbox" checked={value.sensorIds.includes(sensor.id)} onChange={(event) => patch({
          sensorIds: event.target.checked ? [...value.sensorIds, sensor.id] : value.sensorIds.filter((id) => id !== sensor.id),
        })} />{sensor.name}
      </label>)}</fieldset>}
      <label className="gv-checkbox"><input type="checkbox" checked={value.enabled} onChange={(event) => patch({ enabled: event.target.checked })} />Enable monitoring</label>
      <label className="gv-checkbox"><input type="checkbox" checked={!!value.blocksCameraView}
        onChange={(event) => patch({ blocksCameraView: event.target.checked })} />Shape blocks camera coverage</label>
      <div className="gv-detail-actions gv-editor-actions">
        <button type="submit" className="gv-button gv-primary" disabled={!!issue || !value.name.trim() || !labelsValid}>Save boundary</button>
        <button className="gv-button" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  </>;
}
