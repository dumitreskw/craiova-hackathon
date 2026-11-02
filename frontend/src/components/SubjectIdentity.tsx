import { useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, Check, IdCard, Lock, Sparkles, Users } from "lucide-react";
import { api } from "../api";
import type { InvestigationStream } from "../stream";
import type { IdentityCluster, IdentityOverview, Investigation, SocialStatus, SubjectProfile, Target } from "../types";
import { ErrorBox, Modal, Pill, useLoad } from "./ui";

// Who is the subject? The profile form for a person target, the common-name
// warning, and the who's-who picker (backend app/identity.py).

export interface ProfileDraft {
  birthYear: string;
  nationality: string;
  city: string;
  employer: string;
  known: string;
}

export const EMPTY_DRAFT: ProfileDraft = { birthYear: "", nationality: "", city: "", employer: "", known: "" };

const list = (s: string) => s.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);

/** A typed value that will be detected as a person name. */
export function looksLikePerson(value: string): boolean {
  const v = value.trim();
  return v.includes(" ") && !v.includes("@") && !v.includes("://") && !/^\+?[\d\s().-]+$/.test(v);
}

export function draftFrom(p: SubjectProfile | undefined): ProfileDraft {
  return {
    birthYear: p?.birth_year ? String(p.birth_year) : "",
    nationality: (p?.nationalities ?? []).join(", "),
    city: (p?.locations ?? []).join(", "),
    employer: (p?.employers ?? []).join(", "),
    known: [...(p?.emails ?? []), ...(p?.usernames ?? []).map((u) => `@${u}`), ...(p?.phones ?? [])].join(", "),
  };
}

/** The draft as a profile; fields the form doesn't show are kept from `base`. */
export function profileFrom(d: ProfileDraft, base: SubjectProfile = {}): SubjectProfile {
  const known = list(d.known);
  const year = Number.parseInt(d.birthYear, 10);
  return {
    ...base,
    birth_year: Number.isFinite(year) ? year : null,
    nationalities: list(d.nationality),
    locations: list(d.city),
    employers: list(d.employer),
    emails: known.filter((k) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(k)),
    phones: known.filter((k) => /^\+\d[\d\s().-]{6,}$/.test(k)),
    usernames: known
      .filter((k) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(k) && !/^\+\d/.test(k))
      .map((k) => k.replace(/^@/, "")),
  };
}

export function draftIsEmpty(d: ProfileDraft): boolean {
  return Object.values(d).every((v) => !v.trim());
}

const yearOk = (y: string) => !y.trim() || (/^\d{4}$/.test(y.trim()) && +y >= 1900 && +y <= 2030);

/** The fields: what tells this person apart from namesakes. */
export function ProfileFields({ draft, onChange, idPrefix }: {
  draft: ProfileDraft; onChange: (d: ProfileDraft) => void; idPrefix: string;
}) {
  const set = (k: keyof ProfileDraft) => (e: { target: { value: string } }) => onChange({ ...draft, [k]: e.target.value });
  const field = (k: keyof ProfileDraft, label: string, placeholder: string, extra = "") => (
    <div className={extra}>
      <label className="label" htmlFor={`${idPrefix}-${k}`}>{label}</label>
      <input id={`${idPrefix}-${k}`} className="field" value={draft[k]} onChange={set(k)} placeholder={placeholder}
        inputMode={k === "birthYear" ? "numeric" : undefined} maxLength={k === "birthYear" ? 4 : 300}
        aria-invalid={k === "birthYear" && !yearOk(draft.birthYear) ? true : undefined} />
      {k === "birthYear" && !yearOk(draft.birthYear) && <p className="mt-1 text-xs text-danger">A year such as 1985.</p>}
    </div>
  );
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {field("birthYear", "Birth year", "1985")}
      {field("nationality", "Nationality", "Romanian")}
      {field("city", "City", "Craiova")}
      {field("employer", "Employer", "Example SRL")}
      {field("known", "Known emails, usernames or phones", "ion@example.com, @ionp", "sm:col-span-2")}
      <p className="text-xs text-muted sm:col-span-2">
        Results that contradict these details are treated as namesakes; sanctions and wanted-list
        matches with a different birth year or nationality are rejected automatically. Emails,
        usernames and phones are added as targets of their own.
      </p>
    </div>
  );
}

export function profileValid(d: ProfileDraft): boolean {
  return yearOk(d.birthYear);
}

/** Edit a person target's profile. */
function ProfileModal({ investigationId, target, onClose, onSaved }: {
  investigationId: string; target: Target; onClose: () => void; onSaved: () => void;
}) {
  const [draft, setDraft] = useState<ProfileDraft>(() => draftFrom(target.profile));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.put(`/investigations/${investigationId}/targets/${target.id}/profile`,
        { profile: profileFrom(draft, target.profile), scan: true });
      onSaved();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Who is ${target.value}?`} onClose={onClose}>
      <form onSubmit={save} className="space-y-4">
        <ProfileFields draft={draft} onChange={setDraft} idPrefix={`edit-${target.id}`} />
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={busy || !profileValid(draft)}>
            {busy ? "Saving" : "Save and search again"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Pick which same-name person is the subject. */
function factsText(f: SubjectProfile | undefined): string {
  if (!f) return "";
  return [
    f.birth_year ? `born ${f.birth_year}` : "",
    ...(f.nationalities ?? []), ...(f.locations ?? []), ...(f.employers ?? []), ...(f.roles ?? []),
    ...(f.education ?? []),
  ].filter(Boolean).join(", ");
}

function WhosWho({ clusters, onClose, onChosen }: {
  clusters: IdentityCluster[]; onClose: () => void; onChosen: (learned: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function choose(c: IdentityCluster) {
    setBusy(c.id);
    setError(null);
    try {
      const r = await api.post<{ learned?: SubjectProfile; run_ids?: string[] }>(`/identity-clusters/${c.id}/choose`,
        { reason: c.distinguishing ? `matches the subject: ${c.distinguishing}` : `picked "${c.label}" as the subject` });
      const learned = factsText(r.learned);
      onChosen(learned ? `Added to the profile: ${learned}.${r.run_ids?.length ? " Searching again with it." : ""}` : "");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal title="Who's who" onClose={onClose}>
      <p className="mb-4 text-sm text-muted">
        Claude's verification grouped the results with the subject's name into different people.
        Pick the subject: their entities are confirmed and the other people's are rejected. Sanctions
        and wanted-list matches keep their own review.
      </p>
      <ErrorBox error={error} onDismiss={() => setError(null)} />
      <ul className="space-y-3">
        {clusters.map((c) => (
          <li key={c.id} className={`rounded-lg border p-4 ${c.chosen ? "border-accent bg-accent/5" : "border-line"}`}>
            <div className="flex flex-wrap items-start gap-2">
              <h3 className="min-w-0 flex-1 font-semibold">{c.label}</h3>
              {c.chosen ? <Pill tone="accent"><Check className="h-3 w-3" aria-hidden />The subject</Pill>
                : c.is_subject ? <Pill tone="muted"><Sparkles className="h-3 w-3" aria-hidden />Claude: likely the subject</Pill> : null}
              <span className="text-xs text-muted">{c.entity_ids.length} {c.entity_ids.length === 1 ? "entity" : "entities"}</span>
            </div>
            {c.distinguishing && <p className="mt-1 text-sm"><span className="text-muted">Tells them apart: </span>{c.distinguishing}</p>}
            {c.summary && <p className="mt-1 text-sm text-muted">{c.summary}</p>}
            {factsText(c.facts) && (
              <p className="mt-1 text-xs text-muted"><span className="font-medium">Known: </span>{factsText(c.facts)}</p>
            )}
            {!c.chosen && (
              <button className="btn mt-3" disabled={busy !== null} onClick={() => choose(c)}>
                <Check className="h-4 w-4" aria-hidden />{busy === c.id ? "Applying" : "This is the subject"}
              </button>
            )}
          </li>
        ))}
      </ul>
    </Modal>
  );
}

/** Under the target bar: profile buttons, the common-name warning, who's who. */
export function IdentityStrip({ inv, targets, stream, onSaved }: {
  inv: Investigation; targets: Target[]; stream: InvestigationStream; onSaved: () => void;
}) {
  const investigationId = inv.id;
  const people = targets.filter((t) => t.type === "Person");
  const [social] = useLoad(
    () => (people.length ? api.get<SocialStatus>("/social/status") : Promise.resolve(null)),
    [people.length],
  );
  const linkedin = (social?.alerts ?? []).find((a) => a.platform === "linkedin");
  const [notice, setNotice] = useState<string | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const autoVerify = !!inv.settings?.auto_verify;

  async function toggleAuto() {
    setAutoBusy(true);
    try {
      await api.patch(`/investigations/${investigationId}`, { settings: { ...inv.settings, auto_verify: !autoVerify } });
      onSaved();
    } finally {
      setAutoBusy(false);
    }
  }
  const [ov, , , reload] = useLoad(
    () => (people.length ? api.get<IdentityOverview>(`/investigations/${investigationId}/identity`) : Promise.resolve(null)),
    [investigationId, people.length],
  );
  const [editing, setEditing] = useState<Target | null>(null);
  const [whosWho, setWhosWho] = useState(false);

  useEffect(
    () => stream.subscribe((e) => {
      if (["scan.finished", "agent.finished", "identity.updated", "identity.chosen"].includes(e.event)) reload();
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  if (!people.length) return null;
  const clusters = ov?.clusters ?? [];

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      {people.map((t) => {
        const stats = ov?.targets.find((x) => x.target_id === t.id);
        const empty = !stats || draftIsEmpty(draftFrom(stats.profile));
        return (
          <span key={t.id} className="contents">
            <button className="btn btn-quiet h-7 px-2 text-xs" onClick={() => setEditing({ ...t, profile: stats?.profile ?? t.profile })}
              title="Birth year, city, employer and known identifiers: what tells this person apart from namesakes">
              <IdCard className="h-3.5 w-3.5" aria-hidden />
              {empty ? `Describe ${t.value}` : `Profile of ${t.value}`}
            </button>
            {stats?.common_name && (
              <span className="inline-flex items-center gap-1.5 rounded-md border border-warn/50 bg-warn/10 px-2 py-1 text-warn" role="status">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                Common name: {stats.namesakes} {stats.namesakes === 1 ? "namesake" : "namesakes"} and {stats.list_matches} list{" "}
                {stats.list_matches === 1 ? "match" : "matches"} came back.
                <button className="font-semibold underline" onClick={() => setEditing({ ...t, profile: stats.profile })}>Add a detail</button>
              </span>
            )}
          </span>
        );
      })}
      {clusters.length > 0 && (
        <button className="btn btn-quiet h-7 px-2 text-xs" onClick={() => setWhosWho(true)}>
          <Users className="h-3.5 w-3.5" aria-hidden />
          Who's who: {clusters.length} {clusters.length === 1 ? "person" : "people"}
          {clusters.some((c) => c.chosen) ? "" : ", pick the subject"}
        </button>
      )}
      <label className="inline-flex items-center gap-1.5 text-muted" title="After a scan that brings back namesakes or list matches, Claude's verification sorts them on its own">
        <input type="checkbox" className="h-3.5 w-3.5 accent-[rgb(var(--accent))]" checked={autoVerify}
          disabled={autoBusy} onChange={toggleAuto} />
        Sort namesakes automatically
      </label>
      {linkedin && (
        <a href={`#/inv/${investigationId}/social`} role="status" title={linkedin.message}
          className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 ${
            linkedin.level === "error" ? "border-danger/50 bg-danger/10 text-danger" : "border-warn/50 bg-warn/10 text-warn"}`}>
          <Lock className="h-3.5 w-3.5" aria-hidden />
          {linkedin.level === "error" ? "LinkedIn can't be read" : "LinkedIn: no backup account"}
        </a>
      )}
      {notice && <span className="text-muted" role="status">{notice}</span>}
      {editing && (
        <ProfileModal investigationId={investigationId} target={editing} onClose={() => setEditing(null)}
          onSaved={() => { reload(); onSaved(); }} />
      )}
      {whosWho && (
        <WhosWho clusters={clusters} onClose={() => setWhosWho(false)}
          onChosen={(learned) => { reload(); onSaved(); setWhosWho(false); setNotice(learned || null); }} />
      )}
    </div>
  );
}
