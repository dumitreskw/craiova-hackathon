import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { Investigation, LegalBasis, Mode } from "../types";
import { ErrorBox } from "./ui";

const BASES: { value: LegalBasis; label: string; hint: string }[] = [
  { value: "legitimate_interest", label: "Legitimate interest", hint: "Security assessment, vendor or partner vetting." },
  { value: "contract", label: "Contract", hint: "A signed engagement, such as a pentest or due-diligence order." },
  { value: "legal_obligation", label: "Legal obligation", hint: "KYC / AML or another duty set by law." },
  { value: "consent", label: "Consent", hint: "The person investigated agreed to it." },
  { value: "public_task", label: "Public task", hint: "Work carried out in the public interest or by a public authority." },
  { value: "vital_interests", label: "Vital interests", hint: "Protecting someone's life." },
];

export function CreateInvestigation({ onCreated }: { onCreated: (inv: Investigation) => void }) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [basis, setBasis] = useState<LegalBasis>("legitimate_interest");
  const [mode, setMode] = useState<Mode>("passive");
  const [scope, setScope] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [maxDepth, setMaxDepth] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const scopeList = scope
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const purposeShort = purpose.trim().length < 10;
  const activeIncomplete = mode === "active" && (!scopeList.length || !authorization.trim());

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const inv = await api.post<Investigation>("/investigations", {
        name: name.trim(),
        purpose: purpose.trim(),
        legal_basis: basis,
        mode,
        scope: mode === "active" ? scopeList : [],
        authorization: mode === "active" ? authorization.trim() : null,
        settings: { max_depth: maxDepth, connectors: ["*"], exclude_connectors: [] },
      });
      onCreated(inv);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scroll-thin overflow-y-auto">
      <form onSubmit={submit} className="mx-auto w-full max-w-2xl space-y-6 px-6 py-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">New investigation</h1>
          <p className="mt-2 max-w-prose text-muted">
            The purpose and legal basis are stored with the investigation and appear in the audit log. Write them
            for someone reviewing this work later.
          </p>
        </div>

        <div>
          <label className="label" htmlFor="name">Name</label>
          <input id="name" className="field" required maxLength={200} value={name}
            onChange={(e) => setName(e.target.value)} placeholder="Example SRL vendor vetting" />
        </div>

        <div>
          <label className="label" htmlFor="purpose">Purpose</label>
          <textarea id="purpose" className="field min-h-[84px]" required value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="Why this investigation exists and what question it should answer." />
          {purpose && purposeShort && <p className="mt-1 text-xs text-warn">At least 10 characters.</p>}
        </div>

        <fieldset>
          <legend className="label">Legal basis (GDPR Art. 6)</legend>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {BASES.map((b) => (
              <label key={b.value}
                className={`flex cursor-pointer gap-2 rounded-md border px-3 py-2 ${
                  basis === b.value ? "border-accent bg-accent/10" : "border-line hover:border-muted"}`}>
                <input type="radio" name="basis" className="mt-1 accent-[rgb(var(--accent))]"
                  checked={basis === b.value} onChange={() => setBasis(b.value)} />
                <span>
                  <span className="block text-sm font-medium">{b.label}</span>
                  <span className="block text-xs text-muted">{b.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="label">Collection mode</legend>
          <div className="flex gap-2">
            {(["passive", "active"] as Mode[]).map((m) => (
              <button type="button" key={m} onClick={() => setMode(m)} aria-pressed={mode === m}
                className={`btn flex-1 justify-center ${mode === m ? "border-accent bg-accent/10" : ""}`}>
                {m === "passive" ? "Passive only" : "Active allowed"}
              </button>
            ))}
          </div>
          <p className="mt-2 text-sm text-muted">
            {mode === "passive"
              ? "Lookups go to public sources only (DNS, registries, certificate logs, search engines). Nothing is sent to the target's own servers."
              : "Active connectors contact the target's servers, which the owner can see and log. They run only against the hosts listed below, and only with a written authorization on record."}
          </p>
        </fieldset>

        {mode === "active" && (
          <div className="space-y-4 rounded-lg border border-warn/50 bg-warn/5 p-4">
            <div>
              <label className="label" htmlFor="scope">Authorized scope</label>
              <textarea id="scope" className="field min-h-[64px] font-mono" value={scope}
                onChange={(e) => setScope(e.target.value)}
                placeholder={"example.com\n198.51.100.0/24"} />
              <p className="mt-1 text-xs text-muted">
                One host, domain or CIDR per line. Subdomains of a listed domain are included.
              </p>
            </div>
            <div>
              <label className="label" htmlFor="auth">Authorization record</label>
              <textarea id="auth" className="field min-h-[64px]" value={authorization}
                onChange={(e) => setAuthorization(e.target.value)}
                placeholder="Who authorized active testing, when, and the reference (e.g. SOW-42 signed by the client's CISO on 30 Oct 2026)." />
            </div>
          </div>
        )}

        <div>
          <label className="label" htmlFor="depth">Pivot depth for scans</label>
          <select id="depth" className="field w-auto" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
            <option value={0}>0: only the target itself</option>
            <option value={1}>1: the target and what it reveals</option>
            <option value={2}>2: one more step out</option>
            <option value={3}>3: widest (slow, noisy)</option>
          </select>
        </div>

        <ErrorBox error={error} onDismiss={() => setError(null)} />

        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || purposeShort || activeIncomplete}>
            {busy ? "Creating…" : "Create investigation"}
          </button>
          {activeIncomplete && <span className="text-xs text-warn">Active mode needs a scope and an authorization record.</span>}
        </div>
      </form>
    </div>
  );
}
