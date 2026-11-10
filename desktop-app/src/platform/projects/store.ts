import { create } from "zustand";
import { cmd } from "../../lib/tauri";
import type { LatLon } from "../types";
import {
  EMPTY_DOCUMENT,
  KINDS,
  newId,
  parseDocument,
  type MapView,
  type PlacedItem,
  type PlacedKind,
  type Project,
  type ProjectsDocument,
} from "./model";

export interface Placing {
  kind: PlacedKind;
  /** Corners collected so far (area kinds). */
  points: LatLon[];
  /** Set when moving an existing point item instead of creating one. */
  moveItemId?: string;
}

interface ProjectsState extends ProjectsDocument {
  loaded: boolean;
  saveError: string | null;
  selectedItemId: string | null;
  placing: Placing | null;
  load: () => Promise<void>;
  createProject: (name: string, view: MapView) => Project;
  renameProject: (id: string, name: string) => void;
  deleteProject: (id: string) => void;
  setActiveProject: (id: string) => void;
  setProjectView: (id: string, view: MapView) => void;
  selectItem: (id: string | null) => void;
  startPlacing: (kind: PlacedKind) => void;
  startMove: (itemId: string) => void;
  addPlacingPoint: (point: LatLon) => void;
  finishPlacing: () => void;
  cancelPlacing: () => void;
  undoPlacingPoint: () => void;
  updateItem: (id: string, patch: Partial<Omit<PlacedItem, "id" | "kind" | "createdAt">>) => void;
  deleteItem: (id: string) => void;
}

const now = () => new Date().toISOString();

export const useProjectsStore = create<ProjectsState>((set, get) => {
  const updateActive = (fn: (project: Project) => Project) => {
    const { activeProjectId, projects } = get();
    set({ projects: projects.map((project) => (project.id === activeProjectId ? { ...fn(project), updatedAt: now() } : project)) });
  };

  const createItem = (kind: PlacedKind, geometry: Pick<PlacedItem, "position" | "polygon">) => {
    const project = get().projects.find((item) => item.id === get().activeProjectId);
    if (!project) return;
    const count = project.items.filter((item) => item.kind === kind).length + 1;
    const item: PlacedItem = {
      id: newId(kind),
      kind,
      name: `${KINDS[kind].label} ${count}`,
      notes: "",
      ...KINDS[kind].defaults,
      ...geometry,
      createdAt: now(),
      updatedAt: now(),
    };
    updateActive((p) => ({ ...p, items: [...p.items, item] }));
    set({ selectedItemId: item.id, placing: null });
  };

  return {
    ...EMPTY_DOCUMENT,
    loaded: false,
    saveError: null,
    selectedItemId: null,
    placing: null,

    load: async () => {
      try {
        set({ ...parseDocument(await cmd.loadProjects()), loaded: true });
      } catch (error) {
        // Keep `loaded` false so an unreadable file is never overwritten.
        set({ saveError: `Could not read projects: ${String(error)}` });
      }
    },

    createProject: (name, view) => {
      const project: Project = { id: newId("project"), name: name.trim() || "Untitled site", view, items: [], createdAt: now(), updatedAt: now() };
      set({ projects: [...get().projects, project], activeProjectId: project.id, selectedItemId: null });
      return project;
    },
    renameProject: (id, name) =>
      set({ projects: get().projects.map((p) => (p.id === id ? { ...p, name: name || p.name, updatedAt: now() } : p)) }),
    deleteProject: (id) => {
      const projects = get().projects.filter((p) => p.id !== id);
      set({ projects, activeProjectId: get().activeProjectId === id ? projects[0]?.id ?? null : get().activeProjectId, selectedItemId: null, placing: null });
    },
    setActiveProject: (id) => set({ activeProjectId: id, selectedItemId: null, placing: null }),
    setProjectView: (id, view) => set({ projects: get().projects.map((p) => (p.id === id ? { ...p, view, updatedAt: now() } : p)) }),

    selectItem: (selectedItemId) => set({ selectedItemId }),

    startPlacing: (kind) => set({ placing: { kind, points: [] }, selectedItemId: null }),
    startMove: (itemId) => {
      const item = get().projects.flatMap((p) => p.items).find((i) => i.id === itemId);
      if (item && KINDS[item.kind].geometry === "point") set({ placing: { kind: item.kind, points: [], moveItemId: itemId } });
    },
    addPlacingPoint: (point) => {
      const placing = get().placing;
      if (!placing) return;
      if (placing.moveItemId) {
        get().updateItem(placing.moveItemId, { position: point });
        set({ placing: null, selectedItemId: placing.moveItemId });
        return;
      }
      if (KINDS[placing.kind].geometry === "point") {
        createItem(placing.kind, { position: point });
        return;
      }
      set({ placing: { ...placing, points: [...placing.points, point] } });
    },
    finishPlacing: () => {
      const placing = get().placing;
      if (!placing) return;
      if (KINDS[placing.kind].geometry === "area" && placing.points.length >= 3) createItem(placing.kind, { polygon: placing.points });
      else set({ placing: null });
    },
    cancelPlacing: () => set({ placing: null }),
    undoPlacingPoint: () => {
      const placing = get().placing;
      if (placing) set({ placing: { ...placing, points: placing.points.slice(0, -1) } });
    },

    updateItem: (id, patch) =>
      set({
        projects: get().projects.map((p) =>
          p.items.some((i) => i.id === id)
            ? { ...p, updatedAt: now(), items: p.items.map((i) => (i.id === id ? { ...i, ...patch, updatedAt: now() } : i)) }
            : p,
        ),
      }),
    deleteItem: (id) =>
      set({
        projects: get().projects.map((p) => ({ ...p, items: p.items.filter((i) => i.id !== id) })),
        selectedItemId: get().selectedItemId === id ? null : get().selectedItemId,
      }),
  };
});

// Persist to disk shortly after each change, and only after the first load
// so an empty initial state can never overwrite saved projects.
let saveTimer: ReturnType<typeof setTimeout> | null = null;
useProjectsStore.subscribe((state, previous) => {
  if (!state.loaded || (state.projects === previous.projects && state.activeProjectId === previous.activeProjectId)) return;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const { activeProjectId, projects } = useProjectsStore.getState();
    cmd
      .saveProjects({ version: 1, activeProjectId, projects })
      .then(() => useProjectsStore.setState({ saveError: null }))
      .catch((error) => useProjectsStore.setState({ saveError: `Not saved: ${String(error)}` }));
  }, 400);
});

export function useActiveProject(): Project | undefined {
  return useProjectsStore((state) => state.projects.find((project) => project.id === state.activeProjectId));
}
