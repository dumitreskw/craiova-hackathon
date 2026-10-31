import { createContext, useContext } from "react";

export type Role = "viewer" | "analyst" | "admin" | "owner";
export const ROLES: Role[] = ["viewer", "analyst", "admin", "owner"];
const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2, owner: 3 };

export const ROLE_INFO: Record<Role, string> = {
  viewer: "Reads investigations and reports; changes nothing.",
  analyst: "Runs investigations, scans, agents and reports.",
  admin: "Analyst, plus members, invitations and API keys.",
  owner: "Admin, plus owners and the workspace itself.",
};

export interface PlanLimits {
  label: string;
  seats: number | null;
  investigations: number | null;
  api_keys: number | null;
}

export interface Me {
  user: { id: string; email: string; name: string; platform_admin: boolean; via: string };
  org: null | {
    id: string;
    name: string;
    plan: string;
    role: Role;
    limits: PlanLimits;
    usage: { seats: number; investigations: number; api_keys: number; pending_invitations: number };
  };
  orgs: { id: string; name: string; role: Role; plan: string }[];
  auth: { enabled: boolean; account_url: string | null; mcp: boolean };
}

export interface Session {
  me: Me;
  reload: () => void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionContext");
  return s;
}

export function can(me: Me, minimum: Role): boolean {
  const role = me.org?.role;
  return !!role && RANK[role] >= RANK[minimum];
}

export function initials(nameOrEmail: string): string {
  const parts = nameOrEmail.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || "?").slice(0, 2)).toUpperCase();
}
