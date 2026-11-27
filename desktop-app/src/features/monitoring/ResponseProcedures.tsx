import { useEffect, useState } from "react";
import type { Boundary, IncidentAction, ProcedureDefinition, ProcedureStep } from "./monitoringModel";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";

const TRIGGERS = { "door-forced-open": "Forced door", "door-held-open": "Held-open door", "coverage-loss": "Loss of critical camera coverage" } as const;
const KINDS = { review: "Review evidence", question: "Answer yes or no", finding: "Record a finding", contact: "Contact response team", outcome: "Record the outcome" };
const OPTIONS = { review: ["reviewed", "unavailable", "inconclusive"], question: ["yes", "no"], finding: ["recorded"], contact: ["contacted", "unavailable"], outcome: ["recorded"] };
const ANSWERS: Record<string, string> = { reviewed: "Evidence reviewed", unavailable: "Evidence or contact unavailable", inconclusive: "Inconclusive",
  yes: "Yes", no: "No", recorded: "Record finding", contacted: "Response team contacted" };

function template(trigger: ProcedureDefinition["trigger"], boundaryId?: string): ProcedureDefinition {
  return { name: TRIGGERS[trigger], trigger, boundaryId, dueSeconds: 300, steps: [
    { id: "review", kind: "review", text: "Review the incident timeline and available camera evidence.", next: "question" },
    { id: "question", kind: "question", text: "Does the evidence require contacting the response team?", yes: "contact", no: "outcome" },
    { id: "contact", kind: "contact", text: "Contact the response team listed on the incident.", next: "outcome" },
    { id: "outcome", kind: "outcome", text: "Document the outcome and any follow-up." },
  ] };
}

export function ProcedureEditor({ definitions, boundaries, onChange }: {
  definitions: ProcedureDefinition[]; boundaries: Boundary[]; onChange: (value: ProcedureDefinition[]) => void;
}) {
  const [selected, setSelected] = useState(0);
  const [trigger, setTrigger] = useState<ProcedureDefinition["trigger"]>("door-forced-open");
  const [boundary, setBoundary] = useState("");
  const definition = definitions[selected];
  const assignBoundary = boundary || (trigger === "coverage-loss" ? boundaries[0]?.id : undefined);
  const alreadyAssigned = definitions.some((entry) => entry.trigger === trigger && (entry.boundaryId || undefined) === assignBoundary);
  const update = (next: ProcedureDefinition) => onChange(definitions.map((item, index) => index === selected ? next : item));
  function setStep(index: number, next: ProcedureStep) {
    update({ ...definition, steps: definition.steps.map((step, position) => position === index ? next : step) });
  }
  return <details className="gv-triage-section">
    <summary>Response procedures</summary>
    <div className="gv-triage-content gv-incident-form">
      <label>Procedure trigger<select value={trigger} onChange={(event) => setTrigger(event.target.value as typeof trigger)}>
        {Object.entries(TRIGGERS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>Procedure boundary<select value={boundary} onChange={(event) => setBoundary(event.target.value)}>
        <option value="">{trigger === "coverage-loss" ? "First boundary" : "All boundaries"}</option>
        {boundaries.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <button type="button" className="gv-button" disabled={alreadyAssigned || trigger === "coverage-loss" && !assignBoundary}
        onClick={() => { setSelected(definitions.length); onChange([...definitions, template(trigger, assignBoundary)]); }}>Add procedure from template</button>
      {!!definitions.length && <label>Procedure to edit<select value={selected} onChange={(event) => setSelected(Number(event.target.value))}>
        {definitions.map((item, index) => <option key={index} value={index}>{item.name}{item.boundaryId ? " · " + item.boundaryId : ""}</option>)}
      </select></label>}
      {definition && <>
        <label>Procedure name<input value={definition.name} maxLength={80} onChange={(event) => update({ ...definition, name: event.target.value })} /></label>
        <label>Escalate after (seconds)<input type="number" min={30} max={86400} value={definition.dueSeconds}
          onChange={(event) => update({ ...definition, dueSeconds: Number(event.target.value) })} /></label>
        <ol className="gv-triage-records">{definition.steps.map((step, index) => <li key={step.id}>
          <label>Step {index + 1} instruction<input value={step.text} maxLength={300}
            onChange={(event) => setStep(index, { ...step, text: event.target.value })} /></label>
          <label>Step {index + 1} action<select value={step.kind} onChange={(event) => {
            const kind = event.target.value as ProcedureStep["kind"];
            setStep(index, { ...step, kind, ...(kind === "question" ? { yes: step.next, no: step.next } : { next: step.yes ?? step.next }) });
          }}>{Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {(step.kind === "question" ? ["yes", "no"] as const : ["next"] as const).map((edge) =>
            <label key={edge}>Step {index + 1} · {edge === "next" ? "Next" : edge === "yes" ? "If yes" : "If no"}
              <select value={step[edge] ?? ""} onChange={(event) => setStep(index, { ...step, [edge]: event.target.value || null })}>
                <option value="">Finish procedure</option>
                {definition.steps.map((candidate, position) => position > index
                  ? <option key={candidate.id} value={candidate.id}>Step {position + 1} · {candidate.text}</option> : null)}
              </select></label>)}
          <button type="button" className="gv-button" disabled={definition.steps.length < 2} onClick={() =>
            update({ ...definition, steps: definition.steps.filter(({ id }) => id !== step.id).map((entry) => ({
              ...entry, next: entry.next === step.id ? null : entry.next, yes: entry.yes === step.id ? null : entry.yes,
              no: entry.no === step.id ? null : entry.no,
            })) })}>Remove step {index + 1}</button>
        </li>)}</ol>
        <div className="gv-detail-actions">
          <button type="button" className="gv-button" disabled={definition.steps.length >= 20} onClick={() => {
            const id = crypto.randomUUID();
            const steps = definition.steps.map((step, index) => index === definition.steps.length - 1
              ? { ...step, ...(step.kind === "question" ? { yes: id, no: id } : { next: id }) } : step);
            update({ ...definition, steps: [...steps, { id, kind: "finding", text: "Record a finding." }] });
          }}>Add procedure step</button>
          <button type="button" className="gv-button" onClick={() => { onChange(definitions.filter((_, index) => index !== selected)); setSelected(0); }}>Remove procedure</button>
        </div>
      </>}
    </div>
  </details>;
}

type ProcedureState = { version: number; definition: ProcedureDefinition; step: string | null; dueAt: number; escalated: boolean };

export function IncidentProcedure({ session, incidentId, activityCount, canEdit, readOnly, onAction, onReplay }: {
  session: SiteSession; incidentId: string; activityCount: number; canEdit: boolean; readOnly: boolean;
  onAction: (action: IncidentAction) => void; onReplay: () => void;
}) {
  const [state, setState] = useState<ProcedureState | null>(null);
  const [note, setNote] = useState("");
  const [override, setOverride] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ incidentId });
    let timer: number | undefined;
    async function load() {
      try {
        const value = await siteRequest(session, "response-procedure?" + query, undefined, controller.signal) as { procedure: ProcedureState | null };
        if (!controller.signal.aborted) { setState(value.procedure); setError(""); }
      } catch (issue) {
        if (!controller.signal.aborted) setError(issue instanceof Error ? issue.message : "Procedure unavailable.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 5000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [session, incidentId, activityCount]);
  if (error) return <p role="status">{error}</p>;
  if (!state) return null;
  const current = state.definition.steps.find(({ id }) => id === state.step);
  const needsNote = current?.kind === "finding" || current?.kind === "outcome";
  return <section className="gv-details gv-incident-form" aria-label="Guided response">
    <h4>{state.definition.name}</h4>
    <small>Configuration version {state.version} · {state.escalated ? "Escalated" : `Due ${new Date(state.dueAt * 1000).toLocaleString()}`}</small>
    {current ? <>
      <p>{current.text}</p>
      {current.kind === "review" && <button type="button" className="gv-button" onClick={onReplay}>Open procedure evidence</button>}
      {canEdit && <>
        <label>Procedure note<textarea value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} /></label>
        <div className="gv-detail-actions">{OPTIONS[current.kind].map((answer) => <button type="button" className="gv-button" key={answer}
          disabled={needsNote && !note.trim()} onClick={() => {
            onAction({ kind: "procedure-step", actionId: crypto.randomUUID(), step: current.id, answer, text: note });
          }}>{ANSWERS[answer]}</button>)}</div>
      </>}
      {!readOnly && canManageSite(session) && <button type="button" className="gv-button" onClick={() => setOverride(!override)}>Supervisor override</button>}
      {override && !readOnly && <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault();
        onAction({ kind: "procedure-override", actionId: crypto.randomUUID(), reason: note });
      }}>
        <label>Override reason<textarea required value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} /></label>
        <button type="submit" className="gv-button" disabled={!note.trim()}>Record override</button>
      </form>}
    </> : <p role="status">Response procedure complete.</p>}
  </section>;
}
