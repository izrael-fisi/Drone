import {
  AirplaneTakeoffIcon,
  ArrowsOutCardinalIcon,
  BroadcastIcon,
  CrosshairIcon,
  MapPinIcon,
  PlusIcon,
  PolygonIcon,
  ProhibitIcon,
  TrashIcon,
  VideoCameraIcon,
  type Icon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { cn } from "../../lib/utils";
import { usePlatformMapStore } from "../mapStore";
import { EmptyLine, PanelStack, SectionLabel } from "../ui";
import { areaM2, formatArea, KIND_ORDER, KINDS, type PlacedItem, type PlacedKind, type Project } from "./model";
import { useActiveProject, useProjectsStore } from "./store";

const KIND_ICONS: Record<PlacedKind, Icon> = {
  camera: VideoCameraIcon,
  sensor: BroadcastIcon,
  marker: MapPinIcon,
  launch: AirplaneTakeoffIcon,
  geofence: PolygonIcon,
  keepout: ProhibitIcon,
};

const inputClass =
  "w-full rounded-md bg-white/[0.05] px-2.5 py-1.5 text-[13px] text-slate-100 outline-none ring-1 ring-white/10 placeholder:text-slate-600 focus:ring-white/30";

function currentView() {
  return usePlatformMapStore.getState().api?.getView() ?? { center: [39.8283, -98.5795] as [number, number], zoom: 4 };
}

function flyTo(project: Project) {
  usePlatformMapStore.getState().api?.focus(project.view.center, project.view.zoom);
}

function itemCenter(item: PlacedItem): [number, number] | null {
  if (item.position) return item.position;
  if (!item.polygon?.length) return null;
  const lat = item.polygon.reduce((sum, [y]) => sum + y, 0) / item.polygon.length;
  const lon = item.polygon.reduce((sum, [, x]) => sum + x, 0) / item.polygon.length;
  return [lat, lon];
}

function itemMeta(item: PlacedItem) {
  if (item.polygon) return formatArea(areaM2(item.polygon));
  if (item.kind === "camera") return `${Math.round(item.headingDeg ?? 0)}° · ${item.fovDeg ?? 90}° FOV · ${item.rangeM ?? 0} m`;
  if (item.kind === "sensor") return `${item.rangeM ?? 0} m range`;
  return item.position ? `${item.position[0].toFixed(5)}, ${item.position[1].toFixed(5)}` : "";
}

function NumberField({ label, value, unit, min, max, onChange }: { label: string; value: number; unit: string; min: number; max: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="mt-1 flex items-center gap-1.5">
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) onChange(Math.min(max, Math.max(min, next)));
          }}
          className={cn(inputClass, "tabular-nums")}
        />
        <span className="w-6 shrink-0 text-xs text-slate-500">{unit}</span>
      </span>
    </label>
  );
}

function ItemEditor({ item }: { item: PlacedItem }) {
  const updateItem = useProjectsStore((state) => state.updateItem);
  const deleteItem = useProjectsStore((state) => state.deleteItem);
  const startMove = useProjectsStore((state) => state.startMove);
  const center = itemCenter(item);
  const isPoint = KINDS[item.kind].geometry === "point";

  return (
    <div className="space-y-3 border-t border-white/[0.06] px-3 pb-3 pt-3">
      <label className="block">
        <span className="text-xs text-slate-400">Name</span>
        <input className={cn(inputClass, "mt-1")} value={item.name} onChange={(event) => updateItem(item.id, { name: event.target.value })} />
      </label>
      {item.kind === "camera" && (
        <div className="grid grid-cols-3 gap-2">
          <NumberField label="Facing" value={Math.round(item.headingDeg ?? 0)} unit="°" min={0} max={359} onChange={(headingDeg) => updateItem(item.id, { headingDeg })} />
          <NumberField label="Field of view" value={item.fovDeg ?? 90} unit="°" min={1} max={359} onChange={(fovDeg) => updateItem(item.id, { fovDeg })} />
          <NumberField label="Range" value={item.rangeM ?? 60} unit="m" min={1} max={5000} onChange={(rangeM) => updateItem(item.id, { rangeM })} />
        </div>
      )}
      {item.kind === "camera" && (
        <input
          type="range"
          min={0}
          max={359}
          value={Math.round(item.headingDeg ?? 0)}
          onChange={(event) => updateItem(item.id, { headingDeg: Number(event.target.value) })}
          aria-label="Camera facing"
          className="w-full"
        />
      )}
      {item.kind === "sensor" && (
        <NumberField label="Coverage radius" value={item.rangeM ?? 40} unit="m" min={1} max={5000} onChange={(rangeM) => updateItem(item.id, { rangeM })} />
      )}
      <label className="block">
        <span className="text-xs text-slate-400">Notes</span>
        <textarea
          className={cn(inputClass, "mt-1 min-h-[52px] resize-y")}
          value={item.notes}
          placeholder="Mounting height, model, cable run…"
          onChange={(event) => updateItem(item.id, { notes: event.target.value })}
        />
      </label>
      <div className="flex gap-1.5">
        {center && (
          <ActionButton onClick={() => usePlatformMapStore.getState().api?.focus(center, 19)}>
            <CrosshairIcon size={15} /> Show
          </ActionButton>
        )}
        {isPoint && (
          <ActionButton onClick={() => startMove(item.id)}>
            <ArrowsOutCardinalIcon size={15} /> Move
          </ActionButton>
        )}
        <ActionButton
          danger
          onClick={() => {
            if (window.confirm(`Delete ${item.name}?`)) deleteItem(item.id);
          }}
        >
          <TrashIcon size={15} /> Delete
        </ActionButton>
      </div>
    </div>
  );
}

function ActionButton({ children, onClick, danger = false }: { children: ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md text-[13px] transition-colors",
        danger ? "text-red-300 hover:bg-red-500/15" : "text-slate-200 hover:bg-white/[0.08]",
      )}
    >
      {children}
    </button>
  );
}

function NewProjectForm({ onDone, first }: { onDone?: () => void; first: boolean }) {
  const createProject = useProjectsStore((state) => state.createProject);
  const [name, setName] = useState("");
  const create = () => {
    createProject(name || "Untitled site", currentView());
    setName("");
    onDone?.();
  };
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        create();
      }}
    >
      <input autoFocus={!first} className={inputClass} value={name} onChange={(event) => setName(event.target.value)} placeholder="Site name, e.g. Oakland yard" />
      <div className="flex gap-2">
        <button type="submit" className="h-8 flex-1 rounded-md bg-white/90 text-[13px] font-medium text-slate-900 hover:bg-white">
          Create at current map view
        </button>
        {onDone && (
          <button type="button" onClick={onDone} className="h-8 rounded-md px-3 text-[13px] text-slate-300 hover:bg-white/[0.08]">
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

/** Site projects: choose a site, place cameras, sensors and zones on the map, edit them. */
export function ProjectsPanel() {
  const projects = useProjectsStore((state) => state.projects);
  const loaded = useProjectsStore((state) => state.loaded);
  const saveError = useProjectsStore((state) => state.saveError);
  const selectedItemId = useProjectsStore((state) => state.selectedItemId);
  const placing = useProjectsStore((state) => state.placing);
  const { setActiveProject, renameProject, deleteProject, setProjectView, selectItem, startPlacing } = useProjectsStore.getState();
  const project = useActiveProject();
  const [creating, setCreating] = useState(false);

  if (!loaded) {
    return <PanelStack title="Projects" subtitle={saveError ?? "Loading projects…"}>{null}</PanelStack>;
  }

  if (!project || creating) {
    return (
      <PanelStack title="Projects" subtitle="A project is a site on the map with the cameras, sensors and zones you lay out there.">
        <SectionLabel>{projects.length ? "New project" : "Start your first project"}</SectionLabel>
        <p className="px-1 text-[13px] text-slate-400">Move the map to the site first. The project opens at that view.</p>
        <NewProjectForm first={!projects.length} onDone={projects.length ? () => setCreating(false) : undefined} />
      </PanelStack>
    );
  }

  const byKind = KIND_ORDER.map((kind) => ({ kind, items: project.items.filter((item) => item.kind === kind) })).filter((group) => group.items.length);

  return (
    <PanelStack title="Projects" subtitle={saveError ?? `${project.items.length} placed · saved on this computer`}>
      <div className="flex items-center gap-2">
        <select
          value={project.id}
          onChange={(event) => {
            setActiveProject(event.target.value);
            const next = projects.find((p) => p.id === event.target.value);
            if (next) flyTo(next);
          }}
          className={cn(inputClass, "h-9 flex-1 font-medium")}
          aria-label="Active project"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button type="button" onClick={() => setCreating(true)} title="New project" className="flex h-9 w-9 items-center justify-center rounded-md text-slate-300 ring-1 ring-white/10 hover:bg-white/[0.08]">
          <PlusIcon size={16} />
        </button>
      </div>
      <div className="flex gap-1.5">
        <ActionButton onClick={() => flyTo(project)}>Go to site</ActionButton>
        <ActionButton onClick={() => setProjectView(project.id, currentView())}>Set site view here</ActionButton>
        <ActionButton
          onClick={() => {
            const name = window.prompt("Project name", project.name)?.trim();
            if (name) renameProject(project.id, name);
          }}
        >
          Rename
        </ActionButton>
      </div>

      <SectionLabel>Place on map</SectionLabel>
      <div className="grid grid-cols-3 gap-1.5">
        {KIND_ORDER.map((kind) => {
          const KindIcon = KIND_ICONS[kind];
          const active = placing?.kind === kind && !placing.moveItemId;
          return (
            <button
              key={kind}
              type="button"
              onClick={() => (active ? useProjectsStore.getState().cancelPlacing() : startPlacing(kind))}
              aria-pressed={active}
              className={cn(
                "flex flex-col items-center gap-1 rounded-lg px-1 py-2.5 text-xs transition-colors",
                active ? "bg-white/[0.12] text-slate-50" : "bg-white/[0.035] text-slate-300 hover:bg-white/[0.07]",
              )}
            >
              <KindIcon size={20} weight={active ? "fill" : "duotone"} style={{ color: KINDS[kind].color }} />
              {KINDS[kind].label}
            </button>
          );
        })}
      </div>

      {byKind.length === 0 && <EmptyLine text="Nothing placed yet. Pick a type above, then click the map." />}
      {byKind.map(({ kind, items }) => {
        const KindIcon = KIND_ICONS[kind];
        return (
          <div key={kind}>
            <SectionLabel>{KINDS[kind].plural} ({items.length})</SectionLabel>
            <div className="mt-1.5 divide-y divide-white/[0.06] rounded-lg bg-white/[0.035]">
              {items.map((item) => {
                const selected = item.id === selectedItemId;
                return (
                  <div key={item.id}>
                    <button
                      type="button"
                      onClick={() => selectItem(selected ? null : item.id)}
                      className={cn("flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-white/[0.04]", selected && "bg-white/[0.05]")}
                    >
                      <KindIcon size={16} weight="duotone" className="shrink-0" style={{ color: KINDS[kind].color }} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-slate-100">{item.name}</span>
                        <span className="block truncate text-xs tabular-nums text-slate-500">{itemMeta(item)}</span>
                      </span>
                    </button>
                    {selected && <ItemEditor item={item} />}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      <button
        type="button"
        onClick={() => {
          if (window.confirm(`Delete project ${project.name} and its ${project.items.length} placed items?`)) deleteProject(project.id);
        }}
        className="mt-4 px-1 text-xs text-slate-500 hover:text-red-300"
      >
        Delete project
      </button>
    </PanelStack>
  );
}
