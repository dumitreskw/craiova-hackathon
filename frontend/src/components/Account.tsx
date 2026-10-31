import { useState, type FormEvent } from "react";
import { Building2, LogOut, Settings2, ShieldCheck, UserCog, Users } from "lucide-react";
import { api } from "../api";
import { initials, useSession } from "../session";
import { ErrorBox, Spinner } from "./ui";

/** Sidebar footer: who is signed in, the workspace switcher and account links. */
export function AccountMenu({ active }: { active: string }) {
  const { me } = useSession();
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function switchTo(orgId: string) {
    setSwitching(true);
    setError(null);
    try {
      await api.post("/me/org", { org_id: orgId });
      // Investigations, audit and members all belong to the workspace: start clean.
      window.location.hash = "#/";
      window.location.reload();
    } catch (e) {
      setError(e);
      setSwitching(false);
    }
  }

  const label = me.user.name || me.user.email;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2.5 px-1 py-1.5">
        <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent-text">
          {initials(label)}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{label}</p>
          {me.user.email && me.user.email !== label && <p className="truncate text-xs text-muted">{me.user.email}</p>}
        </div>
      </div>

      {me.orgs.length > 1 ? (
        <div className="px-1 pb-1">
          <label className="label flex items-center gap-1.5" htmlFor="org-switch">
            <Building2 className="h-3.5 w-3.5 text-muted" aria-hidden /> Workspace
            {switching && <Spinner className="h-3 w-3" />}
          </label>
          <select id="org-switch" className="field" value={me.org?.id} disabled={switching}
            onChange={(e) => switchTo(e.target.value)}>
            {me.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
      ) : (
        <p className="flex items-center gap-1.5 px-1 pb-1 text-xs text-muted">
          <Building2 className="h-3.5 w-3.5" aria-hidden />
          <span className="truncate">{me.org?.name}</span>
        </p>
      )}
      {error ? <ErrorBox error={error} onDismiss={() => setError(null)} /> : null}

      <NavLink href="#/team" current={active === "team"} icon={<Users className="h-4 w-4" aria-hidden />}>
        Team and access
      </NavLink>
      {me.user.platform_admin && (
        <NavLink href="#/admin" current={active === "admin"} icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>
          Platform admin
        </NavLink>
      )}
      {me.auth.account_url && (
        <NavLink href={me.auth.account_url} external icon={<UserCog className="h-4 w-4" aria-hidden />}>
          Profile and password
        </NavLink>
      )}
      {me.auth.enabled && (
        <form method="post" action="/auth/logout">
          <button type="submit" className="btn btn-quiet btn-sm w-full justify-start">
            <LogOut className="h-4 w-4" aria-hidden /> Sign out
          </button>
        </form>
      )}
    </div>
  );
}

function NavLink({ href, current, external, icon, children }: {
  href: string; current?: boolean; external?: boolean; icon: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <a href={href} aria-current={current ? "page" : undefined}
      {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
      className={`btn btn-quiet btn-sm w-full justify-start ${current ? "bg-raised text-ink" : ""}`}>
      {icon} {children}
    </a>
  );
}

/** Signed in, but in no workspace (an invitation was revoked, or they left the last one). */
export function Onboarding() {
  const { me, reload } = useSession();
  const [name, setName] = useState(`${(me.user.name || me.user.email.split("@")[0]).split(" ")[0]}'s workspace`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/orgs", { name });
      reload();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <div className="panel w-full max-w-md space-y-4 p-6">
      <form onSubmit={create} className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Create a workspace</h1>
          <p className="mt-1.5 text-sm text-muted">
            Investigations, members and API keys live in a workspace. If a teammate invited you, open their
            invitation link instead to join theirs.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="ws-name">Workspace name</label>
          <input id="ws-name" className="field" value={name} minLength={2} maxLength={80} required
            onChange={(e) => setName(e.target.value)} />
        </div>
        {error ? <ErrorBox error={error} /> : null}
        <button type="submit" className="btn btn-primary w-full" disabled={busy}>
          {busy ? <Spinner /> : <Settings2 className="h-4 w-4" aria-hidden />} Create workspace
        </button>
      </form>
      <form method="post" action="/auth/logout" className="flex items-center justify-between gap-2 border-t border-line pt-3">
        <p className="truncate text-xs text-muted">Signed in as {me.user.email}</p>
        <button type="submit" className="btn btn-quiet btn-sm"><LogOut className="h-4 w-4" aria-hidden /> Sign out</button>
      </form>
      </div>
    </div>
  );
}
