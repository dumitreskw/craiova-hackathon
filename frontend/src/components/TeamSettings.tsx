import { useState, type FormEvent, type ReactNode } from "react";
import { Check, Copy, KeyRound, Link2, Trash2, UserPlus } from "lucide-react";
import { api } from "../api";
import { can, ROLE_INFO, ROLES, useSession, type Role } from "../session";
import { ErrorBox, Loading, Meter, Segmented, Spinner, ago, useLoad, when } from "./ui";

interface Member { id: string; email: string; name: string; role: Role; joined_at: string; last_login_at: string | null }
interface Invitation { id: string; email: string; role: Role; created_at: string; expires_at: string }
interface ApiKey {
  id: string; name: string; prefix: string; role: Role; user_id: string; owner_email: string;
  created_at: string; last_used_at: string | null;
}

export function TeamSettings() {
  const { me } = useSession();
  const isAdmin = can(me, "admin");
  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-10 px-6 py-10">
        <header>
          <h1 className="text-[28px] font-semibold leading-tight tracking-tight">Team and access</h1>
          <p className="mt-2 max-w-prose text-[15px] text-muted">
            Who can open this workspace's investigations, what each person may do, and the keys tools use to act in it.
          </p>
        </header>
        <WorkspaceSection />
        <MembersSection />
        {isAdmin && <AddPeopleSection />}
        {can(me, "analyst") && <ApiKeysSection />}
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={`s-${title}`}>
      <h2 id={`s-${title}`} className="section-title">{title}</h2>
      {hint && <p className="mt-1 max-w-prose text-sm text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

// --- workspace -----------------------------------------------------------------------

function WorkspaceSection() {
  const { me, reload } = useSession();
  const org = me.org!;
  const [name, setName] = useState(org.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const isAdmin = can(me, "admin");

  async function rename(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.patch("/orgs/current", { name });
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const meters: [string, number, number | null][] = [
    ["Seats", org.usage.seats, org.limits.seats],
    ["Open investigations", org.usage.investigations, org.limits.investigations],
    ["API keys", org.usage.api_keys, org.limits.api_keys],
  ];
  return (
    <Section title="Workspace" hint={<>You are <strong className="text-ink">{org.role}</strong> here. {ROLE_INFO[org.role]}</>}>
      <div className="grid gap-6 md:grid-cols-[1fr_1fr]">
        <form onSubmit={rename} className="space-y-2">
          <label className="label" htmlFor="org-name">Name</label>
          <div className="flex gap-2">
            <input id="org-name" className="field" value={name} disabled={!isAdmin} minLength={2} maxLength={80}
              onChange={(e) => setName(e.target.value)} />
            {isAdmin && (
              <button className="btn" disabled={busy || name.trim() === org.name || name.trim().length < 2}>
                {busy && <Spinner />} Rename
              </button>
            )}
          </div>
          {error ? <ErrorBox error={error} onDismiss={() => setError(null)} /> : null}
        </form>
        <div>
          <p className="label">{org.limits.label} plan</p>
          <ul className="space-y-2.5">
            {meters.map(([label, used, cap]) => (
              <li key={label}>
                <div className="flex justify-between text-xs">
                  <span className="text-muted">{label}</span>
                  <span>{used}{cap === null ? "" : ` of ${cap}`}</span>
                </div>
                {cap !== null && (
                  <div className="mt-1">
                    <Meter fraction={Math.max(0, 1 - used / cap)} tone={used >= cap ? "danger" : "accent"} label={`${label} left`} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}

// --- members -------------------------------------------------------------------------

function MembersSection() {
  const { me, reload: reloadMe } = useSession();
  const [members, error, , reload] = useLoad(() => api.get<Member[]>("/orgs/current/members"), []);
  const [actionError, setActionError] = useState<unknown>(null);
  const isAdmin = can(me, "admin");
  const isOwner = can(me, "owner");

  async function act(fn: () => Promise<unknown>, leaving = false) {
    setActionError(null);
    try {
      await fn();
      if (leaving) {
        window.location.hash = "#/";
        window.location.reload();
        return;
      }
      reload();
      reloadMe();
    } catch (e) {
      setActionError(e);
    }
  }

  return (
    <Section title="Members">
      {error ? <ErrorBox error={error} /> : null}
      {actionError ? <div className="mb-3"><ErrorBox error={actionError} onDismiss={() => setActionError(null)} /></div> : null}
      {!members && !error && <Loading />}
      {members && (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-2 pr-4 font-medium">Person</th>
              <th className="py-2 pr-4 font-medium">Role</th>
              <th className="py-2 pr-4 font-medium max-sm:hidden">Last sign-in</th>
              <th className="py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const self = m.id === me.user.id;
              const lastOwner = m.role === "owner" && members.filter((x) => x.role === "owner").length === 1;
              const editable = isAdmin && !self && (isOwner || m.role !== "owner");
              return (
                <tr key={m.id} className="border-b border-line/60">
                  <td className="py-2.5 pr-4">
                    <div className="font-medium">{m.name || m.email}{self && <span className="ml-1.5 text-xs text-muted">(you)</span>}</div>
                    <div className="text-xs text-muted">{m.email}</div>
                  </td>
                  <td className="py-2.5 pr-4">
                    {editable ? (
                      <select className="field max-w-[140px]" value={m.role} aria-label={`Role of ${m.email}`}
                        onChange={(e) => act(() => api.patch(`/orgs/current/members/${m.id}`, { role: e.target.value }))}>
                        {ROLES.filter((r) => isOwner || r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    ) : (
                      <span className="capitalize">{m.role}</span>
                    )}
                  </td>
                  <td className="py-2.5 pr-4 text-muted max-sm:hidden">{m.last_login_at ? ago(m.last_login_at) : "Not yet"}</td>
                  <td className="py-2.5 text-right">
                    {(editable || self) && !lastOwner && (
                      <button className="btn btn-quiet btn-sm"
                        onClick={() => act(() => api.del(`/orgs/current/members/${m.id}`), self)}>
                        {self ? "Leave" : "Remove"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Section>
  );
}

// --- invitations & accounts ----------------------------------------------------------

function RoleSelect({ id, value, onChange }: { id: string; value: Role; onChange: (r: Role) => void }) {
  const { me } = useSession();
  return (
    <>
      <select id={id} className="field" value={value} onChange={(e) => onChange(e.target.value as Role)}>
        {ROLES.filter((r) => can(me, "owner") || r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <p className="hint mt-1">{ROLE_INFO[value]}</p>
    </>
  );
}

function CopyField({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <p className="label">{label}</p>
      <div className="flex gap-2">
        <input readOnly className={`field font-mono text-xs ${secret ? "tracking-wide" : ""}`} value={value}
          onFocus={(e) => e.currentTarget.select()} aria-label={label} />
        <button type="button" className="btn" onClick={async () => {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}>
          {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function AddPeopleSection() {
  const { me, reload: reloadMe } = useSession();
  const [mode, setMode] = useState<"invite" | "account">("invite");
  const [invites, , , reloadInvites] = useLoad(() => api.get<Invitation[]>("/orgs/current/invitations"), []);
  const [email, setEmail] = useState("");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<
    null | { kind: "invite"; email: string; url: string } | { kind: "account"; email: string; password: string; url: string }
  >(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      if (mode === "invite") {
        const r = await api.post<{ url: string; email: string }>("/orgs/current/invitations", { email, role });
        setResult({ kind: "invite", email: r.email, url: r.url });
        reloadInvites();
      } else {
        const r = await api.post<{ email: string; temporary_password: string; sign_in_url: string }>(
          "/orgs/current/accounts", { email, first_name: first, last_name: last, role });
        setResult({ kind: "account", email: r.email, password: r.temporary_password, url: r.sign_in_url });
      }
      setEmail("");
      setFirst("");
      setLast("");
      reloadMe();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Add people" hint="Send an invitation link they sign up with, or create the account yourself and hand over a temporary password.">
      <div className="panel p-5">
        {me.auth.enabled && (
          <Segmented label="How to add" value={mode} onChange={(v) => { setMode(v); setResult(null); setError(null); }}
            options={[["invite", "Invitation link"], ["account", "Create account"]] as const} />
        )}
        <form onSubmit={submit} className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className={mode === "account" ? "sm:col-span-2" : ""}>
            <label className="label" htmlFor="add-email">Email</label>
            <input id="add-email" type="email" required className="field" value={email} autoComplete="off"
              onChange={(e) => setEmail(e.target.value)} />
          </div>
          {mode === "account" && (
            <>
              <div>
                <label className="label" htmlFor="add-first">First name</label>
                <input id="add-first" required className="field" value={first} onChange={(e) => setFirst(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="add-last">Last name</label>
                <input id="add-last" className="field" value={last} onChange={(e) => setLast(e.target.value)} />
              </div>
            </>
          )}
          <div>
            <label className="label" htmlFor="add-role">Role</label>
            <RoleSelect id="add-role" value={role} onChange={setRole} />
          </div>
          <div className="sm:col-span-2">
            {error ? <div className="mb-3"><ErrorBox error={error} onDismiss={() => setError(null)} /></div> : null}
            <button className="btn btn-primary" disabled={busy}>
              {busy ? <Spinner /> : mode === "invite" ? <Link2 className="h-4 w-4" aria-hidden /> : <UserPlus className="h-4 w-4" aria-hidden />}
              {mode === "invite" ? "Create invitation link" : "Create account"}
            </button>
          </div>
        </form>

        {result && (
          <div role="status" className="mt-5 space-y-3 rounded-md border border-ok/45 bg-ok/10 p-4">
            {result.kind === "invite" ? (
              <>
                <p className="text-sm">Invitation for <strong>{result.email}</strong> created. Send them this link; it works once, for 7 days.</p>
                <CopyField label="Invitation link" value={result.url} />
              </>
            ) : (
              <>
                <p className="text-sm">
                  Account for <strong>{result.email}</strong> created. Give them the temporary password over a channel you
                  trust; they choose their own at first sign-in. It is not shown again.
                </p>
                <CopyField label="Temporary password" value={result.password} secret />
                <CopyField label="Where they sign in" value={result.url} />
              </>
            )}
          </div>
        )}
      </div>

      {invites && invites.length > 0 && (
        <div className="mt-6">
          <h3 className="text-sm font-medium">Pending invitations</h3>
          <ul className="mt-2 divide-y divide-line/60 border-y border-line/60">
            {invites.map((i) => (
              <li key={i.id} className="flex items-center gap-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{i.email}</span>
                <span className="capitalize text-muted">{i.role}</span>
                <span className="text-xs text-muted max-sm:hidden">expires {when(i.expires_at)}</span>
                <button className="btn btn-quiet btn-sm" onClick={async () => {
                  await api.del(`/orgs/current/invitations/${i.id}`);
                  reloadInvites();
                  reloadMe();
                }}>Revoke</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

// --- API keys ------------------------------------------------------------------------

function ApiKeysSection() {
  const { me, reload: reloadMe } = useSession();
  const [keys, error, , reload] = useLoad(() => api.get<ApiKey[]>("/orgs/current/api-keys"), []);
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<unknown>(null);
  const [created, setCreated] = useState<string | null>(null);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setCreateError(null);
    try {
      const r = await api.post<{ key: string }>("/orgs/current/api-keys", { name, role });
      setCreated(r.key);
      setName("");
      reload();
      reloadMe();
    } catch (err) {
      setCreateError(err);
    } finally {
      setBusy(false);
    }
  }

  const origin = window.location.origin;
  const mcpConfig = created && me.auth.mcp
    ? `claude mcp add --transport http osint ${origin}/mcp --header "Authorization: Bearer ${created}"`
    : null;

  return (
    <Section title="API keys" hint={<>For scripts and for Claude Code or Claude Desktop through <code className="font-mono text-xs">/mcp</code>. A key acts in this workspace with the role you give it, never more than yours.</>}>
      <form onSubmit={create} className="flex flex-wrap items-end gap-3">
        <div className="min-w-[200px] flex-1">
          <label className="label" htmlFor="key-name">Name</label>
          <input id="key-name" required minLength={2} maxLength={60} className="field" placeholder="Claude Code on my laptop"
            value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="w-[160px]">
          <label className="label" htmlFor="key-role">Role</label>
          <select id="key-role" className="field" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? <Spinner /> : <KeyRound className="h-4 w-4" aria-hidden />} Create key
        </button>
      </form>
      {createError ? <div className="mt-3"><ErrorBox error={createError} onDismiss={() => setCreateError(null)} /></div> : null}
      {created && (
        <div role="status" className="mt-4 space-y-3 rounded-md border border-ok/45 bg-ok/10 p-4">
          <p className="text-sm">Copy the key now; it is not shown again.</p>
          <CopyField label="API key" value={created} secret />
          {mcpConfig && <CopyField label="Add it to Claude Code" value={mcpConfig} />}
        </div>
      )}
      {error ? <div className="mt-3"><ErrorBox error={error} /></div> : null}
      {keys && keys.length > 0 && (
        <ul className="mt-5 divide-y divide-line/60 border-y border-line/60">
          {keys.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-sm">
              <span className="min-w-0 flex-1">
                <span className="font-medium">{k.name}</span>
                <span className="ml-2 font-mono text-xs text-muted">{k.prefix}…</span>
              </span>
              <span className="capitalize text-muted">{k.role}</span>
              <span className="text-xs text-muted">{k.user_id === me.user.id ? "yours" : k.owner_email}</span>
              <span className="text-xs text-muted">{k.last_used_at ? `used ${ago(k.last_used_at)}` : "never used"}</span>
              <button className="icon-btn" aria-label={`Revoke ${k.name}`} title="Revoke" onClick={async () => {
                await api.del(`/orgs/current/api-keys/${k.id}`);
                reload();
                reloadMe();
              }}>
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
