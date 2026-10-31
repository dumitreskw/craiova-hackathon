import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { CreditEntry } from "./types";
import type { Tone } from "./components/ui";

// One shared copy of GET /credits for the sidebar, the agents tab and the
// sources page. The server caches provider checks for ~5 minutes; refresh=true
// asks every provider again.

interface State {
  data: CreditEntry[] | null;
  error: unknown;
  missing: boolean; // no /credits endpoint, or not a platform admin
  loading: boolean;
  at: number;
}

let state: State = { data: null, error: null, missing: false, loading: false, at: 0 };
const listeners = new Set<(s: State) => void>();
const STALE_MS = 5 * 60 * 1000;

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l(state));
}

export async function loadCredits(refresh = false): Promise<void> {
  if (state.loading) return;
  set({ loading: true });
  try {
    const data = await api.get<CreditEntry[]>(`/credits${refresh ? "?refresh=true" : ""}`);
    set({ data, error: null, missing: false, loading: false, at: Date.now() });
  } catch (e) {
    // 403: balances of the platform's provider accounts are for platform admins.
    const missing = e instanceof ApiError && (e.status === 404 || e.status === 403);
    set({ error: missing ? null : e, missing, loading: false, at: Date.now() });
  }
}

export function useCredits(): State & { refresh: () => void } {
  const [s, setS] = useState(state);
  useEffect(() => {
    listeners.add(setS);
    if (!state.loading && Date.now() - state.at > STALE_MS) loadCredits();
    const t = window.setInterval(() => loadCredits(), STALE_MS);
    return () => {
      listeners.delete(setS);
      window.clearInterval(t);
    };
  }, []);
  return { ...s, refresh: () => loadCredits(true) };
}

export interface Reading {
  /** e.g. "412 of 500 left" */
  headline: string;
  /** 0..1 share still available, when it can be computed */
  left: number | null;
  tone: Tone;
}

/** Turn whatever numbers a provider gave into one line and a meter. */
export function readCredit(c: CreditEntry): Reading {
  const unit = c.unit ? ` ${c.unit}` : "";
  const n = (x: number) => x.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (c.status === "unavailable") return { headline: c.note || "Not configured", left: null, tone: "muted" };
  if (c.status === "error") return { headline: "Could not check the balance", left: null, tone: "danger" };
  let remaining = c.remaining;
  if (remaining === null && c.limit !== null && c.used !== null) remaining = Math.max(0, c.limit - c.used);
  if (remaining !== null && c.limit) {
    const left = remaining / c.limit;
    return { headline: `${n(remaining)} of ${n(c.limit)}${unit} left`, left, tone: toneFor(left) };
  }
  if (remaining !== null) return { headline: `${n(remaining)}${unit} left`, left: null, tone: remaining <= 0 ? "danger" : "ok" };
  if (c.used !== null) return { headline: `${n(c.used)}${unit} used`, left: null, tone: "muted" };
  return { headline: "Balance shown only in the provider's dashboard", left: null, tone: "muted" };
}

function toneFor(left: number): Tone {
  if (left <= 0.05) return "danger";
  if (left <= 0.2) return "warn";
  return "ok";
}
