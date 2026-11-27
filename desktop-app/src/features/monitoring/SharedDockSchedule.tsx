import { useEffect, useRef, useState } from "react";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";

type Appointment = { id: string; boundaryId: string; startsAt: number; endsAt: number; status?: string };
type Schedule = { day: string; timeZone: string; today: boolean; revision: number; history: { revision: number; publishedAt: number }[];
  bays: { id: string; name: string; occupancy: string; changedAt: number | null }[]; appointments: Appointment[];
  unassigned: { id: string; cameraId: string; boundaryId: string; occurredAt: number }[];
  checkins: { id: string; boundaryId: string; appointmentId: string | null; reference: string; checkedAt: number }[] };
type Preview = { revision: number; baseRevision: number; appointments: Appointment[]; published: boolean };

export function SharedDockSchedule({ session }: { session: SiteSession }) {
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState("");
  const [result, setResult] = useState<{ day: string; data: Schedule }>();
  const [preview, setPreview] = useState<Preview>();
  const [bay, setBay] = useState("");
  const [appointment, setAppointment] = useState("");
  const [reference, setReference] = useState("");
  const [pending, setPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const checkIn = useRef<{ fingerprint: string; id: string }>();
  const manage = canManageSite(session);
  const data = result?.day === day ? result.data : undefined;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      try {
        const values = await siteRequest(session, "dock-schedules" + (day ? "?day=" + encodeURIComponent(day) : ""), undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]));
        if (!controller.signal.aborted) setResult({ day, data: values });
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load the dock schedule.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 4000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [open, day, session, refresh]);

  async function submit(body: object, done: (value: Preview) => void) {
    if (!data || pending) return;
    setPending(true); setError(""); setNotice("");
    try {
      done(await siteRequest(session, "dock-schedules", { ...body, day: data.day }));
      setRefresh((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The dock schedule request failed.");
    } finally {
      setPending(false);
    }
  }

  const names = new Map(data?.bays.map(({ id, name }) => [id, name]));
  const time = (seconds: number) => new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: data?.timeZone ?? "UTC",
  }).format(seconds * 1000);

  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Shared dock schedule</summary>
    {open && <section className="gv-triage-content gv-incident-form" aria-label="Shared dock schedule">
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!data && !error && <p role="status">Loading dock shift…</p>}
      {data && <>
        <label>Site day<input type="date" disabled={pending} value={day || data.day} onChange={(event) => {
          setDay(event.target.value); setPreview(undefined); setBay(""); setAppointment(""); setNotice(""); setError("");
        }} /></label>
        <p>{data.timeZone} · {data.revision ? "Published schedule " + data.revision : "No published schedule"}</p>
        {manage && !!data.bays.length && <>
          <label>Dock appointments CSV<input type="file" accept=".csv,text/csv" disabled={pending} onChange={async (event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            if (!file) return;
            if (file.size > 100_000) { setError("Choose a dock CSV up to 100 KB."); return; }
            try {
              const csv = await file.text();
              await submit({ operation: "preview", csv }, (value) => {
                setPreview(value.published ? undefined : value);
                setNotice(value.published ? "This schedule is already published." : "Schedule preview ready.");
              });
            } catch { setError("Could not read the dock CSV."); }
          }} /></label>
          <small>Use appointmentId,boundaryId,startsAt,endsAt and timezone-qualified times for the selected site day.</small>
          {preview && <section className="gv-details" aria-label="Schedule preview">
            <h5>Schedule preview</h5>
            <ul className="gv-triage-records">{preview.appointments.map((row) => <li key={row.id}>
              <strong>{row.id} · {names.get(row.boundaryId)}</strong><small>{time(row.startsAt)} – {time(row.endsAt)}</small>
            </li>)}</ul>
            <button type="button" className="gv-button" disabled={pending || preview.baseRevision !== data.revision}
              onClick={() => void submit({ operation: "publish", revision: preview.revision, baseRevision: preview.baseRevision }, () => {
                setPreview(undefined); setNotice("Dock schedule published.");
              })}>Publish schedule</button>
          </section>}
        </>}
        {!!data.bays.length && <section aria-label="Shared bay status">
          <h5>Bay status</h5>
          <ul className="gv-triage-records">{data.bays.map((item) => <li key={item.id}>
            <strong>{item.name} · {item.occupancy}</strong>
            {item.changedAt !== null && <small>Occupied since {time(item.changedAt)}</small>}
          </li>)}</ul>
        </section>}
        {!!data.appointments.length && <section aria-label="Published appointments">
          <h5>Appointments</h5>
          <ul className="gv-triage-records">{data.appointments.map((row) => <li key={row.id}>
            <strong>{row.id} · {row.status}</strong><small>{names.get(row.boundaryId)} · {time(row.startsAt)} – {time(row.endsAt)}</small>
          </li>)}</ul>
        </section>}
        {!!data.unassigned.length && <section aria-label="Unassigned dock arrivals">
          <h5>Unassigned arrivals</h5>
          <ul className="gv-triage-records">{data.unassigned.map((item) => <li key={item.cameraId + "/" + item.id}>
            <strong>{names.get(item.boundaryId)} · Unassigned arrival</strong><small>{time(item.occurredAt)}</small>
          </li>)}</ul>
        </section>}
        {session.operator.role !== "viewer" && data.today && !!data.bays.length && <form className="gv-incident-form" onSubmit={(event) => {
          event.preventDefault();
          const body = { operation: "check-in", boundaryId: bay, appointmentId: appointment || null, reference: reference.trim(),
            revision: data.revision };
          const fingerprint = JSON.stringify([data.day, body]);
          if (checkIn.current?.fingerprint !== fingerprint) checkIn.current = { fingerprint, id: crypto.randomUUID() };
          void submit({ ...body, id: checkIn.current.id }, () => {
            setReference(""); setAppointment(""); checkIn.current = undefined; setNotice("Check-in saved.");
          });
        }}>
          <label>Check-in bay<select value={bay} onChange={(event) => { setBay(event.target.value); setAppointment(""); }}>
            <option value="">Choose bay</option>{data.bays.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
          </select></label>
          <label>Appointment<select value={appointment} onChange={(event) => setAppointment(event.target.value)}>
            <option value="">Unscheduled</option>{data.appointments.filter(({ boundaryId }) => boundaryId === bay)
              .map(({ id }) => <option key={id} value={id}>{id}</option>)}
          </select></label>
          <label>Shipment or appointment reference<input value={reference} maxLength={80} onChange={(event) => setReference(event.target.value)} /></label>
          <button type="submit" className="gv-button" disabled={pending || !bay || !reference.trim()}>Save check-in</button>
        </form>}
        {!!data.checkins.length && <section aria-label="Dock check-ins">
          <h5>Check-ins</h5>
          <ul className="gv-triage-records">{data.checkins.map((item) => <li key={item.id}>
            <strong>{item.reference} · {names.get(item.boundaryId)}</strong>
            <small>{time(item.checkedAt)}{item.appointmentId && " · " + item.appointmentId}</small>
          </li>)}</ul>
        </section>}
        {!!data.history.length && <details className="gv-triage-section">
          <summary>Published schedule history</summary>
          <ul className="gv-triage-records">{data.history.map((entry) => <li key={entry.revision}>
            <strong>Schedule {entry.revision}</strong><small>{time(entry.publishedAt)}</small>
          </li>)}</ul>
        </details>}
      </>}
    </section>}
  </details>;
}
