import { Component, type ErrorInfo, type ReactNode } from "react";

/** Last-resort screen: a render error shows what failed and a reload, never a blank window. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Proxigo render error", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex h-screen items-center justify-center bg-bg-base p-8 text-slate-200">
        <div className="max-w-lg">
          <h1 className="text-lg font-semibold text-slate-100">Proxigo hit an error and stopped drawing this screen.</h1>
          <p className="mt-2 text-sm text-slate-400">Reload to continue. If it happens again, send this message to support.</p>
          <pre className="mt-4 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-white/[0.04] p-3 font-mono text-xs text-red-300">
            {error.message}
            {"\n"}
            {error.stack?.split("\n").slice(1, 6).join("\n")}
          </pre>
          <button type="button" className="btn-primary mt-4" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
