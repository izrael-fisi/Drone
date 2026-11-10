/**
 * Dock geometry, shared by the shell (dock width) and the map host (where
 * floating HUDs start). Tailwind needs literal class names, so each state is
 * spelled out.
 *
 * - expanded: labelled sidebar, no panel open (224px)
 * - rail: icon rail, either collapsed by the operator or because a panel is open (60px)
 * - panel: rail + 384px panel
 */
export type DockMode = "expanded" | "rail" | "panel";

export function dockMode(panelOpen: boolean, sidebarCollapsed: boolean): DockMode {
  if (panelOpen) return "panel";
  return sidebarCollapsed ? "rail" : "expanded";
}

export const DOCK_WIDTH_CLASS: Record<DockMode, string> = {
  expanded: "w-[224px]",
  rail: "w-[60px]",
  panel: "w-[444px]",
};

/** Left offset for top-left map HUDs: 12px margin + dock + 16px gap. */
export const HUD_LEFT_CLASS: Record<DockMode, string> = {
  expanded: "left-[252px]",
  rail: "left-[88px]",
  panel: "left-[472px]",
};
