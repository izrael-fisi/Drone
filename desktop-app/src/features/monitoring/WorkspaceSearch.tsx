import { useEffect, useId, useRef, useState } from "react";
import { Bell, Network, Pentagon, Radio, Search, X } from "lucide-react";
import { labelKey } from "./monitoringModel";

export type SearchTarget = { kind: "sensor" | "boundary" | "alert" | "gateway"; id: string; name: string; detail: string; terms: string[] };
const icons = { sensor: Radio, boundary: Pentagon, alert: Bell, gateway: Network };
const resultKey = (target: SearchTarget) => target.kind + target.id;

export function WorkspaceSearch({ targets, onClose, onSelect }: { targets: SearchTarget[]; onClose: () => void; onSelect: (target: SearchTarget) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState("");
  const tokens = labelKey(query.trim()).split(/\s+/).filter(Boolean);
  const matches = targets.filter((target) => {
    const text = labelKey([target.name, target.detail, ...target.terms].join(" "));
    return tokens.every((token) => text.includes(token));
  });
  const results = matches.slice(0, 24);
  const activeIndex = Math.max(0, results.findIndex((result) => resultKey(result) === active));
  const optionId = (index: number) => listId + "-option-" + index;

  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    input.current?.focus();
    return () => node?.close();
  }, []);

  useEffect(() => {
    dialog.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, results[activeIndex]?.id]);

  function close() {
    dialog.current?.close();
    onClose();
  }

  function choose(target: SearchTarget) {
    dialog.current?.close();
    onSelect(target);
  }

  return <dialog ref={dialog} className="gv-search" aria-label="Search workspace"
    onCancel={(event) => { event.preventDefault(); close(); }}
    onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
    <div className="gv-search-content">
      <div className="gv-search-field"><Search aria-hidden="true" size={19} />
        <input ref={input} role="combobox" value={query} aria-label="Find a device, boundary, or alert" placeholder="Find a device, boundary, or alert"
          aria-autocomplete="list" aria-expanded={results.length > 0} aria-controls={listId}
          aria-activedescendant={results.length ? optionId(activeIndex) : undefined} autoComplete="off"
          onChange={(event) => { setQuery(event.target.value); setActive(""); }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || !results.length) return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const direction = event.key === "ArrowDown" ? 1 : -1;
              setActive(resultKey(results[(activeIndex + direction + results.length) % results.length]));
            } else if (event.key === "Enter") {
              event.preventDefault();
              choose(results[activeIndex]);
            }
          }} />
        <button type="button" className="gv-icon-button" aria-label="Close search" onClick={close}><X aria-hidden="true" size={17} /></button>
      </div>
      {results.length > 0 && <p className="gv-sr-only" role="status" aria-atomic="true">
        {matches.length > results.length ? `Showing ${results.length} of ${matches.length} results` : `${results.length} ${results.length === 1 ? "result" : "results"}`}
      </p>}
      <div className="gv-search-results" id={listId} role="listbox" aria-label="Search results" hidden={!results.length}>
        {results.map((result, index) => {
          const Icon = icons[result.kind];
          return <div role="option" id={optionId(index)} key={resultKey(result)} aria-selected={index === activeIndex}
            data-active={index === activeIndex} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(result)}>
            <Icon aria-hidden="true" size={16} /><span><strong>{result.name}</strong><small>{result.detail}</small></span>
          </div>;
        })}
      </div>
      {!results.length && <p className="gv-empty" role="status">No matching devices, boundaries, or alerts.</p>}
    </div>
  </dialog>;
}
