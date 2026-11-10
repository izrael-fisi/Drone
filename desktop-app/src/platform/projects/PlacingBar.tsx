import { ArrowUUpLeftIcon, CheckIcon, XIcon } from "@phosphor-icons/react";
import { useEffect } from "react";
import { KINDS } from "./model";
import { useProjectsStore } from "./store";

/** Floating instructions and controls while a site item is being placed or drawn. */
export function PlacingBar() {
  const placing = useProjectsStore((state) => state.placing);
  const finish = useProjectsStore((state) => state.finishPlacing);
  const cancel = useProjectsStore((state) => state.cancelPlacing);
  const undo = useProjectsStore((state) => state.undoPlacingPoint);

  useEffect(() => {
    if (!placing) return;
    // Capture phase so Esc cancels placement instead of also closing the dock.
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "Escape") cancel();
      else if (event.key === "Enter") finish();
      else if (event.key === "Backspace") undo();
      else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [cancel, finish, placing, undo]);

  if (!placing) return null;
  const kind = KINDS[placing.kind];
  const area = kind.geometry === "area";
  const corners = placing.points.length;
  const message = placing.moveItemId
    ? `Click the new position for this ${kind.label.toLowerCase()}.`
    : area
      ? corners < 3
        ? `Click the map to add corners (${corners} of at least 3).`
        : `${corners} corners. Keep clicking, or finish the ${kind.label.toLowerCase()}.`
      : `Click the map to place the ${kind.label.toLowerCase()}.`;

  return (
    <div className="absolute left-1/2 top-4 z-[1350] flex -translate-x-1/2 items-center gap-3 rounded-full border border-white/12 bg-bg-base/70 py-1.5 pl-4 pr-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.45)] backdrop-blur-xl">
      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: kind.color }} />
      <span className="text-[13px] text-slate-200">{message}</span>
      {area && corners > 0 && (
        <button type="button" onClick={undo} title="Undo last corner (Backspace)" className="flex h-8 w-8 items-center justify-center rounded-full text-slate-300 hover:bg-white/10">
          <ArrowUUpLeftIcon size={16} />
        </button>
      )}
      {area && (
        <button
          type="button"
          onClick={finish}
          disabled={corners < 3}
          className="flex h-8 items-center gap-1.5 rounded-full bg-white/90 px-3 text-[13px] font-medium text-slate-900 hover:bg-white disabled:opacity-40"
        >
          <CheckIcon size={14} weight="bold" /> Finish
        </button>
      )}
      <button type="button" onClick={cancel} title="Cancel (Esc)" className="flex h-8 w-8 items-center justify-center rounded-full text-slate-300 hover:bg-white/10">
        <XIcon size={16} />
      </button>
    </div>
  );
}
