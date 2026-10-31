import { useState } from "react";
import { api } from "../api";
import { ErrorBox, Loading, useLoad, when } from "./ui";

interface OrgRow {
  id: string; name: string; plan: string; created_at: string; disabled_at: string | null;
  usage: { seats: number; investigations: number; api_keys: number; pending_invitations: number };
}

/** Operators only: every workspace on the platform, its plan and usage. */
export function PlatformAdmin() {
  const [orgs, error, , reload] = useLoad(() => api.get<OrgRow[]>("/platform/orgs"), []);
  const [plans] = useLoad(() => api.get<Record<string, { label: string }>>("/plans"), []);
  const [actionError, setActionError] = useState<unknown>(null);

  async function update(id: string, body: { plan?: string; disabled?: boolean }) {
    setActionError(null);
    try {
      await api.patch(`/platform/orgs/${id}`, body);
      reload();
    } catch (e) {
      setActionError(e);
    }
  }

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-10">
        <h1 className="text-[28px] font-semibold leading-tight tracking-tight">Platform admin</h1>
        <p className="mt-2 max-w-prose text-[15px] text-muted">
          Every workspace on this platform. Plans set the limits on seats, open investigations and API keys; a suspended
          workspace's members can no longer open it. Accounts themselves are managed in Keycloak.
        </p>
        <div className="mt-6 space-y-3">
          {error ? <ErrorBox error={error} /> : null}
          {actionError ? <ErrorBox error={actionError} onDismiss={() => setActionError(null)} /> : null}
        </div>
        {!orgs && !error && <Loading />}
        {orgs && (
          <table className="mt-4 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-4 font-medium">Workspace</th>
                <th className="py-2 pr-4 font-medium">Plan</th>
                <th className="py-2 pr-4 text-right font-medium">Seats</th>
                <th className="py-2 pr-4 text-right font-medium">Investigations</th>
                <th className="py-2 pr-4 font-medium max-md:hidden">Created</th>
                <th className="py-2"><span className="sr-only">Status</span></th>
              </tr>
            </thead>
            <tbody>
              {orgs.map((o) => (
                <tr key={o.id} className={`border-b border-line/60 ${o.disabled_at ? "opacity-60" : ""}`}>
                  <td className="py-2.5 pr-4">
                    <div className="font-medium">{o.name}</div>
                    <div className="font-mono text-2xs text-muted">{o.id}</div>
                  </td>
                  <td className="py-2.5 pr-4">
                    <select className="field max-w-[150px]" value={o.plan} aria-label={`Plan of ${o.name}`}
                      onChange={(e) => update(o.id, { plan: e.target.value })}>
                      {Object.entries(plans || { [o.plan]: { label: o.plan } }).map(([k, v]) => (
                        <option key={k} value={k}>{v.label}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2.5 pr-4 text-right font-mono text-[13px]">{o.usage.seats}</td>
                  <td className="py-2.5 pr-4 text-right font-mono text-[13px]">{o.usage.investigations}</td>
                  <td className="whitespace-nowrap py-2.5 pr-4 text-muted max-md:hidden">{when(o.created_at)}</td>
                  <td className="py-2.5 text-right">
                    <button className={`btn btn-sm ${o.disabled_at ? "" : "btn-danger"}`}
                      onClick={() => update(o.id, { disabled: !o.disabled_at })}>
                      {o.disabled_at ? "Reinstate" : "Suspend"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
