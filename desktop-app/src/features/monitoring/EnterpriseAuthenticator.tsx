import { useEffect, useState, type FormEvent } from "react";

const API = "/api/enterprise/supabase/mfa";
type Factor = { id: string; name: string };
type Enrollment = { id: string; secret: string; qr: string };

export function EnterpriseAuthenticator() {
  const [factors, setFactors] = useState<Factor[]>();
  const [factor, setFactor] = useState("");
  const [enrollment, setEnrollment] = useState<Enrollment>();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const request = new AbortController();
    async function load() {
      try {
        const response = await fetch(API, { signal: request.signal, cache: "no-store", redirect: "error" });
        if (!response.ok) throw new Error();
        const data: { factors: Factor[] } = await response.json();
        setFactors(data.factors);
        setFactor(data.factors[0]?.id ?? "");
      } catch {
        if (!request.signal.aborted) setError("Sign-in attempt expired. Start sign-in again.");
      }
    }
    void load();
    return () => request.abort();
  }, []);

  async function enroll() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(API + "/enroll", { method: "POST", redirect: "error" });
      if (!response.ok) throw new Error();
      const data: Enrollment = await response.json();
      setEnrollment(data);
      setFactor(data.id);
    } catch {
      setError("Authenticator setup could not be started. Start sign-in again.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(API + "/verify", { method: "POST", redirect: "error",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ factor, code }) });
      if (!response.ok) throw new Error();
      location.replace("/");
    } catch {
      setError("Verification failed. Check the code or start sign-in again.");
      setCode("");
      setBusy(false);
    }
  }

  return <div className="gv-app gv-enterprise-entry">
    <main className="gv-enterprise-card" aria-labelledby="gv-enterprise-heading">
      <img src="/proxigo-mark.png" width={32} height={32} alt="" />
      <h1 id="gv-enterprise-heading">{factors?.length === 0 ? "Set up your authenticator" : "Verify your sign-in"}</h1>
      {!factors && !error && <p role="status">Checking your sign-in…</p>}
      {factors?.length === 0 && !enrollment && <>
        <p>Add Global View to an authenticator app to protect your operator account.</p>
        <button className="gv-button gv-primary" type="button" onClick={() => void enroll()} disabled={busy}>Set up authenticator</button>
      </>}
      {enrollment && <>
        <p>Scan the QR code in your authenticator app or enter the setup key.</p>
        <img className="gv-enterprise-qr" width={144} height={144} alt="Authenticator setup QR code"
          src={"data:image/svg+xml," + encodeURIComponent(enrollment.qr)} />
        <p>Setup key <code className="gv-enterprise-setup-key">{enrollment.secret}</code></p>
      </>}
      {factor && <form className="gv-incident-form" onSubmit={(event) => void verify(event)}>
        {factors && factors.length > 1 && <label>Authenticator<select value={factor} onChange={(event) => setFactor(event.target.value)}>
          {factors.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
        </select></label>}
        <label>Authenticator code<input name="code" inputMode="numeric" autoComplete="one-time-code" required
          pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value)} /></label>
        <button className="gv-button gv-primary" type="submit" disabled={busy}>Verify</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      <a className="gv-button" href="/">Back to sign-in</a>
    </main>
  </div>;
}
