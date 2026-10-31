import { useCallback, useEffect, useState } from "react";
import { FolderSearch, Monitor, Moon, Plus, Sun } from "lucide-react";
import { api, getAnalyst, setAnalyst } from "./api";
import type { Investigation } from "./types";
import { CreateInvestigation } from "./components/CreateInvestigation";
import { CreditsSummary, CreditsView } from "./components/CreditsView";
import { TABS, Workspace, type Tab } from "./components/Workspace";
import { Badge, ErrorBox, useLoad, when } from "./components/ui";
import { go } from "./nav";

type Route = { page: "home" } | { page: "new" } | { page: "sources" } | { page: "inv"; id: string; tab: Tab };

function parseHash(): Route {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "new") return { page: "new" };
  if (parts[0] === "sources") return { page: "sources" };
  if (parts[0] === "inv" && parts[1]) {
    const tab = (TABS as readonly string[]).includes(parts[2]) ? (parts[2] as Tab) : "graph";
    return { page: "inv", id: parts[1], tab };
  }
  return { page: "home" };
}

type Theme = "system" | "dark" | "light";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("osint.theme") as Theme) || "system";
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

const THEME_ICON = { system: Monitor, dark: Moon, light: Sun };
const THEME_LABEL = { system: "Match system", dark: "Dark", light: "Light" };

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [theme, cycleTheme] = useTheme();
  const [analyst, setAnalystState] = useState(getAnalyst);
  const [list, listError, , reloadList] = useLoad(() => api.get<Investigation[]>("/investigations"), []);
  const [health] = useLoad(() => api.get<{ build?: string }>("/health"), []);

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
  const ThemeIcon = THEME_ICON[theme];

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[264px] shrink-0 flex-col border-r border-line bg-panel max-md:hidden">
        <a href="#/" className="flex items-center gap-2.5 px-4 pb-3 pt-4">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-[15px] font-semibold tracking-tight">OSINT Workbench</span>
          {health?.build && (
            <span className="ml-auto rounded border border-line px-1.5 py-px font-mono text-[11px] text-muted"
              title={`Build ${health.build} (first 8 characters of the commit)`}>
              {health.build}
            </span>
          )}
        </a>
        <div className="px-3 pb-3">
          <a href="#/new" className={`btn btn-primary w-full ${route.page === "new" ? "ring-2 ring-accent/40" : ""}`}>
            <Plus className="h-4 w-4" aria-hidden /> New investigation
          </a>
        </div>
        <p className="px-4 pb-1 text-xs font-medium text-muted" id="inv-nav">Investigations</p>
        <nav className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-labelledby="inv-nav">
          {listError ? <div className="px-1"><ErrorBox error={listError} /></div> : null}
          {list?.length === 0 && <p className="px-2 py-3 text-sm text-muted">None yet.</p>}
          {list?.map((inv) => {
            const active = inv.id === activeId;
            return (
              <a key={inv.id} href={`#/inv/${inv.id}/graph`} aria-current={active ? "page" : undefined}
                className={`relative mb-0.5 block rounded-md px-3 py-2 transition-colors duration-150 hover:bg-raised ${
                  active ? "bg-raised" : ""}`}>
                {active && <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-accent" aria-hidden />}
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{inv.name}</span>
                  {inv.mode === "active" && <Badge value="active" title="Active collection" />}
                </div>
                {inv.stats && (
                  <div className="mt-0.5 text-xs text-muted">
                    {inv.stats.entities.toLocaleString()} entities, {inv.stats.findings.toLocaleString()} lookups
                  </div>
                )}
              </a>
            );
          })}
        </nav>
        <div className="space-y-3 border-t border-line p-3">
          <CreditsSummary />
          <div className="px-1">
            <label className="label" htmlFor="analyst">Your name in the audit log</label>
            <input id="analyst" className="field" placeholder="analyst" value={analyst}
              onChange={(e) => {
                setAnalystState(e.target.value);
                setAnalyst(e.target.value);
              }} />
          </div>
          <button className="btn btn-quiet btn-sm w-full justify-start" onClick={cycleTheme}
            aria-label={`Theme: ${THEME_LABEL[theme]}. Click to change.`}>
            <ThemeIcon className="h-4 w-4" aria-hidden /> Theme: {THEME_LABEL[theme]}
          </button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        {route.page === "new" && <CreateInvestigation onCreated={onCreated} />}
        {route.page === "home" && <Home list={list} />}
        {route.page === "sources" && <CreditsView />}
        {route.page === "inv" && (
          <Workspace key={route.id} id={route.id} tab={route.tab} onChanged={reloadList} />
        )}
      </main>
    </div>
  );
}

function Home({ list }: { list: Investigation[] | null }) {
  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-12">
        <h1 className="text-[28px] font-semibold leading-tight tracking-tight">Investigations</h1>
        <p className="mt-2 max-w-prose text-[15px] text-muted">
          Each investigation records why it exists and what you may touch. Every lookup is logged with its source, so
          any fact in the graph or a report traces back to where it came from.
        </p>
        <div className="mt-6 flex gap-2">
          <a href="#/new" className="btn btn-primary"><Plus className="h-4 w-4" aria-hidden /> New investigation</a>
          <a href="#/sources" className="btn">Sources and credits</a>
        </div>
        {list && list.length > 0 && (
          <table className="mt-10 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-4 font-medium">Name and purpose</th>
                <th className="py-2 pr-4 font-medium">Mode</th>
                <th className="py-2 pr-4 text-right font-medium">Entities</th>
                <th className="py-2 font-medium">Opened</th>
              </tr>
            </thead>
            <tbody>
              {list.map((inv) => (
                <tr key={inv.id} className="group border-b border-line/60">
                  <td className="py-3 pr-4">
                    <a href={`#/inv/${inv.id}/graph`} className="font-medium group-hover:underline">{inv.name}</a>
                    <div className="line-clamp-1 max-w-xl text-muted">{inv.purpose}</div>
                  </td>
                  <td className="py-3 pr-4"><Badge value={inv.mode} /></td>
                  <td className="py-3 pr-4 text-right font-mono text-[13px]">{inv.stats?.entities.toLocaleString() ?? "—"}</td>
                  <td className="whitespace-nowrap py-3 text-muted">{when(inv.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {list?.length === 0 && (
          <div className="mt-10 flex items-start gap-4 rounded-lg border border-dashed border-line p-6">
            <FolderSearch className="mt-0.5 h-6 w-6 text-muted" aria-hidden />
            <div>
              <p className="font-medium">No investigations yet</p>
              <p className="mt-1 text-sm text-muted">Start one with a purpose; targets come next.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
