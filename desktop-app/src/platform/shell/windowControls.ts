

export function hasTauriWindowRuntime() {
  if (typeof window === "undefined") return false;
  const tauriInternals = (
    window as Window & { __TAURI_INTERNALS__?: { invoke?: unknown; metadata?: unknown } }
  ).__TAURI_INTERNALS__;
  return typeof tauriInternals?.invoke === "function" && Boolean(tauriInternals.metadata);
}

export async function runWindowAction(action: "minimize" | "toggleMaximize" | "close") {
  if (!hasTauriWindowRuntime()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const appWindow = getCurrentWindow();
  await appWindow[action]();
}
