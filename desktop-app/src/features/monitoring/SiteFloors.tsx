import { useState } from "react";
import { httpEndpointUrl, isPosition, type SiteFloor } from "./monitoringModel";

export function FloorSelect({ floors, value, onChange, empty = "All locations", label = "Site level" }: {
  floors: SiteFloor[]; value?: string; onChange: (id: string) => void; empty?: string; label?: string;
}) {
  if (!floors.length) return null;
  const buildings = [...new Set(floors.map((floor) => floor.building))];
  return <label className="gv-incident-field">{label}<select value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
    <option value="">{empty}</option>{buildings.map((building) => <optgroup key={building} label={building}>
      {floors.filter((floor) => floor.building === building).map((floor) => <option key={floor.id} value={floor.id}>{floor.name}</option>)}
    </optgroup>)}
  </select></label>;
}

export function SiteFloors({ floors, active, onSelect, onSave, newId }: {
  floors: SiteFloor[]; active: string; onSelect: (id: string) => void; onSave: (floor: SiteFloor) => void; newId: () => string;
}) {
  const [edit, setEdit] = useState("");
  const [building, setBuilding] = useState("");
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [northwest, setNorthwest] = useState("");
  const [southeast, setSoutheast] = useState("");
  const [error, setError] = useState("");
  function load(id: string) {
    const floor = floors.find((value) => value.id === id);
    setEdit(id); setBuilding(floor?.building ?? ""); setName(floor?.name ?? ""); setImage(floor?.imageUrl ?? "");
    setNorthwest(floor?.corners[0].join(", ") ?? ""); setSoutheast(floor?.corners[2].join(", ") ?? ""); setError("");
  }
  function point(input: string) {
    const parts = input.split(",");
    const coordinates = parts.map(Number);
    return parts.length === 2 && parts.every((part) => part.trim()) && isPosition(coordinates) ? coordinates : undefined;
  }
  return <details className="gv-triage-section gv-floor-settings">
    <summary>Buildings and floor plans</summary>
    <div className="gv-triage-content gv-incident-form">
      <FloorSelect floors={floors} value={active} onChange={onSelect} empty="Outdoors" />
      <FloorSelect floors={floors} value={edit} onChange={load} empty="Add a floor" label="Floor to configure" />
      <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault();
        const nw = point(northwest), se = point(southeast), imageUrl = httpEndpointUrl(image);
        if (!nw || !se || nw[0] >= se[0] || nw[1] <= se[1] || se[0] - nw[0] >= 1 || nw[1] - se[1] >= 1) {
          setError("Use northwest and southeast longitude, latitude coordinates for a local north-up floor plan."); return;
        }
        if (!imageUrl || !building.trim() || !name.trim()) { setError("Enter a building name, floor name, and accessible image URL."); return; }
        const corners: SiteFloor["corners"] = [nw, [se[0], nw[1]], se, [nw[0], se[1]]];
        const floor = { id: edit || newId(), building: building.trim(), name: name.trim(), imageUrl, corners };
        onSave(floor); setEdit(floor.id); setError("");
      }}>
        <label>Building name<input required maxLength={60} value={building} onChange={(event) => setBuilding(event.target.value)} /></label>
        <label>Floor name<input required maxLength={40} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Floor plan image URL<input required type="url" value={image} onChange={(event) => setImage(event.target.value)} /></label>
        <label>Northwest corner (longitude, latitude)<input required value={northwest} onChange={(event) => setNorthwest(event.target.value)} /></label>
        <label>Southeast corner (longitude, latitude)<input required value={southeast} onChange={(event) => setSoutheast(event.target.value)} /></label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" className="gv-button">Save floor plan</button>
      </form>
    </div>
  </details>;
}
