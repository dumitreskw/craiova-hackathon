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
  const [maxDepth, setMaxDepth] = useState(1);
  const [costly, setCostly] = useState<"none" | "metered" | "all">("all");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const purposeShort = purpose.trim().length < 10;

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
        settings: { max_depth: maxDepth, connectors: ["*"], exclude_connectors: [],
                    costly_connectors: costly },
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
          <h1 className="text-[28px] font-semibold leading-tight tracking-tight">New investigation</h1>
          <p className="mt-2 max-w-prose text-[15px] text-muted">
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
                  basis === b.value ? "border-accent bg-accent/10" : "border-line hover:border-muted/70"}`}>
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
                className={`btn flex-1 ${mode === m ? "border-accent bg-accent/10" : ""}`}>
                {m === "passive" ? "Passive only" : "Active allowed"}
              </button>
            ))}
          </div>
          <p className="mt-2 text-sm text-muted">
            {mode === "passive"
              ? "Lookups go to public sources only (DNS, registries, certificate logs, search engines). Nothing is sent to the target's own servers."
              : "Active connectors contact the target's servers, which the owner can see and log."}
          </p>
        </fieldset>

        <div>
          <label className="label" htmlFor="depth">Pivot depth for scans</label>
          <select id="depth" className="field w-auto" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
            <option value={0}>0: only the target itself</option>
            <option value={1}>1: the target and what it reveals</option>
            <option value={2}>2: one more step out</option>
            <option value={3}>3: widest (slow, noisy)</option>
          </select>
        </div>

        <div>
          <label className="label" htmlFor="costly">Sources with quotas or fees in automatic scans</label>
          <select id="costly" className="field w-auto" value={costly}
            onChange={(e) => setCostly(e.target.value as "none" | "metered" | "all")}>
            <option value="all">Everything: free, monthly quota (Hunter, Tomba) and paid (DeHashed)</option>
            <option value="metered">Free and monthly-quota sources (Hunter, Tomba)</option>
            <option value="none">Free sources only</option>
          </select>
          <p className="mt-1 text-xs text-muted">Quotas are shared by everyone on this server and enforced per month. <a className="text-accent-text hover:underline" href="#/sources">See what is left</a>.</p>
        </div>

        <ErrorBox error={error} onDismiss={() => setError(null)} />

        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || purposeShort}>
            {busy ? "Creating…" : "Create investigation"}
          </button>
        </div>
      </form>
    </div>
  );
}
