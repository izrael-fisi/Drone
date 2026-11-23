import { useState, type FormEvent } from "react";
import { SITE_ROLES, siteRequest, type SiteRole, type SiteSession } from "./SiteOperator";

type Member = { subject: string; name: string; role: SiteRole; cameras: "*" | string[]; active: boolean; hasToken: boolean; changedAt: string };

export function OrganizationAccounts({ session }: { session: SiteSession }) {
  const [members, setMembers] = useState<Member[]>();
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [issued, setIssued] = useState("");
  if (session.operator.role !== "org-admin") return null;
  const active = members?.find(({ subject }) => subject === selected);

  async function refresh() {
    setBusy(true); setError("");
    try { setMembers((await siteRequest(session, "accounts")).accounts); }
    catch (issue) { setError(issue instanceof Error ? issue.message : "Could not load organization accounts."); }
    finally { setBusy(false); }
  }

  async function change(operation: string, subject: string, fields: Record<string, unknown> = {}) {
    setBusy(true); setError(""); setNotice(""); setIssued("");
    try {
      const result = await siteRequest(session, "accounts", { operation, subject, ...fields });
      const member: Member = result.account;
      setMembers((prior) => [...(prior ?? []).filter((row) => row.subject !== member.subject), member].sort((a, b) => a.name.localeCompare(b.name)));
      setSelected(member.subject);
      if (typeof result.token === "string") setIssued(result.token);
      setNotice(operation === "rotate-token" ? "New site token issued. The previous token and active viewing links were revoked." : "Organization account updated.");
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not update the account."); }
    finally { setBusy(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const raw = String(fields.get("cameras")).trim();
    void change(active ? "access" : "create", active?.subject ?? String(fields.get("subject")).trim(), {
      name: String(fields.get("name") ?? "").trim(), role: fields.get("role"), cameras: raw === "*" ? "*" : raw.split(/[,\s]+/).filter(Boolean),
    });
  }

  return <details className="gv-triage-section" onToggle={(event) => { if (event.currentTarget.open && !members && !busy) void refresh(); }}>
    <summary>Organization accounts</summary>
    <div className="gv-triage-content gv-incident-form">
      <p>{session.operator.organization.name} · {session.operator.site}</p>
      <label>Organization member<select value={selected ?? ""} onChange={(event) => { setSelected(event.target.value || undefined); setIssued(""); setNotice(""); }}>
        <option value="">Add account</option>{members?.map((member) => <option key={member.subject} value={member.subject}>{member.name}{member.active ? "" : " · Disabled"}</option>)}
      </select></label>
      <form className="gv-incident-form" key={`${active?.subject ?? "new"}:${active?.changedAt ?? ""}`} onSubmit={submit}>
        {active ? <p>{active.subject} · {active.active ? "Active" : "Disabled"} · {active.hasToken ? "Site token set" : "Directory sign-in or token required"}</p> : <>
          <label>Account subject<input name="subject" required maxLength={200} placeholder="Directory subject or local account ID" /></label>
          <label>Operator display name<input name="name" required maxLength={80} /></label>
        </>}
        <label>Organization role<select name="role" defaultValue={active?.role ?? "operator"}>
          {Object.entries(SITE_ROLES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Camera scope<input name="cameras" defaultValue={active?.cameras === "*" ? "*" : active?.cameras.join(", ") ?? ""} placeholder="Camera IDs, or * for all" /></label>
        <button type="submit" className="gv-button" disabled={busy || active?.subject === session.operator.subject}>{active ? "Save role and scope" : "Create organization account"}</button>
      </form>
      {active && active.subject !== session.operator.subject && <div className="gv-detail-actions">
        <button className="gv-button" type="button" disabled={busy} onClick={() => void change(active.active ? "disable" : "enable", active.subject)}>{active.active ? "Disable account" : "Enable account"}</button>
        <button className="gv-button" type="button" disabled={busy || !active.active} onClick={() => void change("rotate-token", active.subject)}>{active.hasToken ? "Rotate site token" : "Generate site token"}</button>
        {active.hasToken && <button className="gv-button" type="button" disabled={busy} onClick={() => void change("revoke-token", active.subject)}>Revoke site token</button>}
      </div>}
      {issued && <label>New site token (shown once)<input readOnly value={issued} onFocus={(event) => event.target.select()} /></label>}
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
