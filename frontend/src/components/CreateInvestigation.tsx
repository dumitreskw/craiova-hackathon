import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { Investigation, Mode } from "../types";
import { ErrorBox } from "./ui";

export function CreateInvestigation({ onCreated }: { onCreated: (inv: Investigation) => void }) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [mode, setMode] = useState<Mode>("passive");
  const [maxDepth, setMaxDepth] = useState(1);
  const [costly, setCostly] = useState<"none" | "metered" | "all">("all");
  const [autoVerify, setAutoVerify] = useState(true);
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
        mode,
        settings: { max_depth: maxDepth, connectors: ["*"], exclude_connectors: [],
                    costly_connectors: costly, auto_verify: autoVerify },
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
            The purpose is stored with the investigation and appears in the audit log. Write it
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
            <option value="all">Free and monthly-quota sources (Hunter, Tomba)</option>
            <option value="none">Free sources only</option>
          </select>
          <p className="mt-1 text-xs text-muted">Quotas are shared by everyone on this server and enforced per month. Paid sources (DeHashed) never run in automatic scans; a Claude agent run uses them only when you allow paid tools for that run. <a className="text-accent-text hover:underline" href="#/sources">See what is left</a>.</p>
        </div>

        <label className="flex items-start gap-3">
          <input type="checkbox" className="mt-1 h-4 w-4 accent-[rgb(var(--accent))]" checked={autoVerify}
            onChange={(e) => setAutoVerify(e.target.checked)} />
          <span>
            <span className="text-sm font-medium">Sort namesakes automatically</span>
            <span className="mt-0.5 block text-xs text-muted">
              When a scan brings back other people with the subject's name or list matches, Claude's verification
              runs on its own (a few lookups) so the who's-who is ready when you open the investigation.
            </span>
          </span>
        </label>

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
