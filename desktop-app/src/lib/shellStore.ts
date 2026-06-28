import { create } from "zustand";

export type RightDockRoute =
  | "root"
  | "maps"
  | "vehicle"
  | "camera"
  | "calibration"
  | "flights"
  | "settings"
  | "mav"
  | "diagnostics-settings"
  | "account";
export type BottomDockTabId = "system-status" | "diagnostics" | "parameters" | "messages" | "ekf-init" | "console";

export interface MapSearchTarget {
  id: string;
  label: string;
  detail?: string;
  lat: number;
  lon: number;
  zoom?: number;
}

interface ShellState {
  rightDockOpen: boolean;
  rightDockStack: RightDockRoute[];
  rightDockDirection: "forward" | "back";
  bottomDockOpen: boolean;
  bottomDockTab: BottomDockTabId;
  mapSearchTarget: MapSearchTarget | null;
  setRightDockOpen: (open: boolean) => void;
  pushRightDock: (route: RightDockRoute) => void;
  popRightDock: () => void;
  popRightDockTo: (depth: number) => void;
  resetRightDock: () => void;
  setBottomDockOpen: (open: boolean) => void;
  setBottomDockTab: (tab: BottomDockTabId) => void;
  setMapSearchTarget: (target: MapSearchTarget | null) => void;
}

export const useShellStore = create<ShellState>((set) => ({
  rightDockOpen: false,
  rightDockStack: [],
  rightDockDirection: "forward",
  bottomDockOpen: false,
  bottomDockTab: "system-status",
  mapSearchTarget: null,
  setRightDockOpen: (rightDockOpen) => set({ rightDockOpen }),
  pushRightDock: (route) =>
    set((state) => ({
      rightDockOpen: true,
      rightDockStack: state.rightDockStack[state.rightDockStack.length - 1] === route
        ? state.rightDockStack
        : [...state.rightDockStack, route],
      rightDockDirection: "forward",
    })),
  popRightDock: () =>
    set((state) => ({
      rightDockStack: state.rightDockStack.length > 0 ? state.rightDockStack.slice(0, -1) : [],
      rightDockDirection: "back",
    })),
  popRightDockTo: (depth) =>
    set((state) => ({
      rightDockStack: state.rightDockStack.slice(0, Math.max(0, depth)),
      rightDockDirection: "back",
    })),
  resetRightDock: () => set({ rightDockStack: [], rightDockOpen: false, rightDockDirection: "back" }),
  setBottomDockOpen: (bottomDockOpen) => set({ bottomDockOpen }),
  setBottomDockTab: (bottomDockTab) => set({ bottomDockTab, bottomDockOpen: true }),
  setMapSearchTarget: (mapSearchTarget) => set({ mapSearchTarget }),
}));
