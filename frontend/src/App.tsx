import { useCallback, useEffect, useState } from "react";
import { api, getAnalyst, setAnalyst } from "./api";
import type { Investigation } from "./types";
import { CreateInvestigation } from "./components/CreateInvestigation";
import { Workspace, type Tab } from "./components/Workspace";
import { Badge, ErrorBox, useLoad } from "./components/ui";
import { go } from "./nav";

type Route = { page: "home" } | { page: "new" } | { page: "inv"; id: string; tab: Tab };

const TABS: Tab[] = ["graph", "material", "runs", "reports", "audit"];

function parseHash(): Route {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "new") return { page: "new" };
  if (parts[0] === "inv" && parts[1]) {
    const tab = (TABS as string[]).includes(parts[2]) ? (parts[2] as Tab) : "graph";
    return { page: "inv", id: parts[1], tab };
  }
  return { page: "home" };
}

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("osint.theme") || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("osint.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  const cycle = () => setTheme((t) => (t === "system" ? "dark" : t === "dark" ? "light" : "system"));
  return [theme, cycle];
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [theme, cycleTheme] = useTheme();
  const [analyst, setAnalystState] = useState(getAnalyst);
  const [list, listError, , reloadList] = useLoad(() => api.get<Investigation[]>("/investigations"), []);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const onCreated = useCallback(
    (inv: Investigation) => {
      reloadList();
      go(`/inv/${inv.id}/graph`);
    },
    [reloadList],
  );

  const activeId = route.page === "inv" ? route.id : null;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-panel max-md:hidden">
        <div className="flex items-center gap-2 px-4 pb-3 pt-4">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          <a href="#/" className="font-semibold tracking-tight">
            OSINT Workbench
          </a>
        </div>
        <div className="px-3 pb-2">
          <a href="#/new" className="btn btn-primary w-full justify-center">
            New investigation
          </a>
        </div>
        <nav className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Investigations">
          {listError ? <ErrorBox error={listError} /> : null}
          {list?.length === 0 && <p className="px-2 py-3 text-sm text-muted">No investigations yet.</p>}
          {list?.map((inv) => (
            <a
              key={inv.id}
              href={`#/inv/${inv.id}/graph`}
              className={`mb-0.5 block rounded-md px-2.5 py-2 hover:bg-raised ${
                inv.id === activeId ? "bg-raised ring-1 ring-line" : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{inv.name}</span>
                {inv.mode === "active" && <Badge value="active" title="Active collection authorized" />}
              </div>
              <div className="mt-0.5 text-2xs text-muted">
                {inv.stats ? `${inv.stats.entities} entities, ${inv.stats.findings} findings` : ""}
              </div>
            </a>
          ))}
        </nav>
        <div className="border-t border-line p-3">
          <label className="label" htmlFor="analyst">
            Your name (recorded in the audit log)
          </label>
          <input
            id="analyst"
            className="field"
            placeholder="analyst"
            value={analyst}
            onChange={(e) => {
              setAnalystState(e.target.value);
              setAnalyst(e.target.value);
            }}
          />
          <button className="btn btn-quiet mt-2 w-full justify-center text-xs" onClick={cycleTheme}>
            Theme: {theme}
          </button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        {route.page === "new" && <CreateInvestigation onCreated={onCreated} />}
        {route.page === "home" && <Home list={list} />}
        {route.page === "inv" && (
          <Workspace key={route.id} id={route.id} tab={route.tab} onChanged={reloadList} />
        )}
      </main>
    </div>
  );
}

function Home({ list }: { list: Investigation[] | null }) {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Investigations</h1>
      <p className="mt-2 max-w-prose text-muted">
        Each investigation records why it exists and what you are allowed to touch. Every lookup is logged with
        its source, so any fact in the graph or a report can be traced back to where it came from.
      </p>
      <div className="mt-6 flex gap-2 md:hidden">
        <a href="#/new" className="btn btn-primary">
          New investigation
        </a>
      </div>
      <ul className="mt-8 divide-y divide-line border-y border-line">
        {list?.map((inv) => (
          <li key={inv.id}>
            <a href={`#/inv/${inv.id}/graph`} className="flex items-baseline gap-4 py-3 hover:bg-panel">
              <span className="font-medium">{inv.name}</span>
              <span className="min-w-0 flex-1 truncate text-sm text-muted">{inv.purpose}</span>
              <Badge value={inv.mode} />
            </a>
          </li>
        ))}
        {list?.length === 0 && (
          <li className="py-6 text-muted">
            Nothing here yet. <a className="text-accent underline" href="#/new">Start an investigation</a> with a
            purpose and a legal basis.
          </li>
        )}
      </ul>
    </div>
  );
}
