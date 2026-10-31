import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import {
  Bot, Check, FileCheck2, Network, ScrollText, ShieldCheck, TerminalSquare, Users, type LucideIcon,
} from "lucide-react";
import { typeColor } from "../entityStyle";

const SIGN_UP = "/auth/register?next=/app/";
const SIGN_IN = "/auth/login?next=/app/";
const APP = "/app/";

/** Is there a session already? Then the page offers the workspace instead. */
function useSignedIn(): boolean {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => {
    fetch("/api/v1/me", { headers: { Accept: "application/json" } })
      .then((r) => setSignedIn(r.ok))
      .catch(() => setSignedIn(false));
  }, []);
  return signedIn;
}

export function Landing() {
  const signedIn = useSignedIn();
  return (
    <div className="min-h-screen bg-ground text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-ink">
        Skip to content
      </a>
      <Header signedIn={signedIn} />
      <main id="main">
        <Hero signedIn={signedIn} />
        <Sources />
        <Process />
        <Features />
        <Pricing signedIn={signedIn} />
        <ClosingCta signedIn={signedIn} />
      </main>
      <Footer />
    </div>
  );
}

function Wrap({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-6xl px-4 sm:px-6 ${className}`}>{children}</div>;
}

function PrimaryCta({ signedIn, large }: { signedIn: boolean; large?: boolean }) {
  const size = large ? "min-h-[48px] px-6 text-[15px]" : "";
  return signedIn
    ? <a href={APP} className={`btn btn-primary cursor-pointer ${size}`}>Open your workspace</a>
    : <a href={SIGN_UP} className={`btn btn-primary cursor-pointer ${size}`}>Create an account</a>;
}

// --- header --------------------------------------------------------------------------------

const NAV: [string, string][] = [["#how", "How it works"], ["#features", "Product"], ["#pricing", "Pricing"]];

function Header({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="sticky top-0 z-40 border-b border-line/60 bg-ground/75 backdrop-blur-md supports-[backdrop-filter]:bg-ground/60">
      <Wrap className="flex h-16 items-center gap-6">
        <a href="/" className="flex shrink-0 items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-[15px] font-semibold tracking-tight">Cybnetic OSINT</span>
        </a>
        <nav aria-label="Sections" className="hidden items-center gap-1 md:flex">
          {NAV.map(([href, label]) => (
            <a key={href} href={href}
              className="rounded-md px-3 py-2 text-sm text-muted transition-colors duration-150 hover:bg-raised hover:text-ink">
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {!signedIn && (
            <a href={SIGN_IN} className="btn btn-quiet cursor-pointer max-[400px]:hidden">Sign in</a>
          )}
          <PrimaryCta signedIn={signedIn} />
        </div>
      </Wrap>
    </header>
  );
}

// --- hero ----------------------------------------------------------------------------------

function Hero({ signedIn }: { signedIn: boolean }) {
  return (
    <section className="hero-backdrop overflow-hidden border-b border-line/60">
      <Wrap className="relative pb-16 pt-16 sm:pt-24">
        <div className="max-w-3xl">
          <h1 className="text-[40px] font-semibold leading-[1.04] tracking-[-0.03em] sm:text-[60px] lg:text-[68px]">
            Investigations you can defend, fact by fact.
          </h1>
          <p className="mt-6 max-w-[58ch] text-[17px] leading-[1.6] text-muted sm:text-lg">
            Start from a person, a company or a domain. Cybnetic queries open sources, follows every lead into a live
            graph, and hands you a report in which each claim links to the lookup that found it.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <PrimaryCta signedIn={signedIn} large />
            {!signedIn && <a href={SIGN_IN} className="btn min-h-[48px] cursor-pointer px-6 text-[15px]">Sign in</a>}
          </div>
          <ul className="mt-7 flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted">
            {["Workspaces isolated per team", "Append-only audit log", "Passive collection by default"].map((t) => (
              <li key={t} className="flex items-center gap-2">
                <Check className="h-4 w-4 text-ok" aria-hidden /> {t}
              </li>
            ))}
          </ul>
        </div>
        <ProductWindow />
      </Wrap>
    </section>
  );
}

// The hero's product shot: a small, real-looking slice of the workbench.
// Fictional company on reserved names and documentation address ranges.
interface GNode { id: string; type: string; label: string; x: number; y: number; state?: "target" | "confirmed" | "unverified" | "selected" }
const NODES: GNode[] = [
  { id: "d", type: "Domain", label: "example-srl.ro", x: 250, y: 170, state: "target" },
  { id: "ip", type: "IPAddress", label: "203.0.113.24", x: 95, y: 92 },
  { id: "cert", type: "Certificate", label: "*.example-srl.ro", x: 255, y: 40 },
  { id: "org", type: "Organization", label: "Example SRL", x: 100, y: 258 },
  { id: "ana", type: "Person", label: "Ana Popescu", x: 420, y: 96, state: "selected" },
  { id: "mail", type: "Email", label: "a.popescu@…", x: 430, y: 250 },
  { id: "dup", type: "Person", label: "Ana Popescu, Cluj", x: 280, y: 318, state: "unverified" },
];
const EDGES: [string, string, boolean?][] = [
  ["d", "ip"], ["d", "cert"], ["d", "org"], ["org", "ana"], ["d", "mail"], ["mail", "ana"], ["ana", "dup", true],
];

function ProductWindow() {
  const at = (id: string) => NODES.find((n) => n.id === id)!;
  return (
    <figure className="relative mt-14 sm:mt-16">
      <div aria-hidden className="pointer-events-none absolute -inset-x-10 -top-12 bottom-0"
        style={{ background: "radial-gradient(ellipse 55% 45% at 50% 20%, rgb(var(--accent) / 0.16), transparent 70%)" }} />
      <div className="relative overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_30px_80px_-30px_rgb(0_0_0/0.6)]">
        <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
          <span aria-hidden className="flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-line" />
            <span className="h-2.5 w-2.5 rounded-full bg-line" />
            <span className="h-2.5 w-2.5 rounded-full bg-line" />
          </span>
          <span className="truncate text-xs text-muted">Vendor due diligence: Example SRL</span>
          <span className="ml-auto hidden gap-1 sm:flex" aria-hidden>
            {["Graph", "Agents", "Reports", "Audit log"].map((t, i) => (
              <span key={t} className={`rounded px-2 py-0.5 text-xs ${i === 0 ? "bg-raised text-ink" : "text-muted"}`}>{t}</span>
            ))}
          </span>
        </div>
        <div className="grid lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <div className="graph-surface relative border-line lg:border-r">
            <svg viewBox="0 0 520 360" className="block h-auto w-full" role="img"
              aria-label="Graph: the domain example-srl.ro linked to its IP address, certificate, company, an email address and the company's director Ana Popescu; a second Ana Popescu is an unverified match.">
              {EDGES.map(([a, b, dashed], i) => {
                const A = at(a), B = at(b);
                return (
                  <line key={`${a}-${b}`} x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                    className="graph-edge" style={{ "--i": i } as CSSProperties}
                    stroke={dashed ? "rgb(var(--danger))" : "rgb(var(--line))"} strokeWidth={dashed ? 1.5 : 1.25}
                    strokeDasharray={dashed ? "5 5" : undefined} pathLength={dashed ? undefined : 1} />
                );
              })}
              {NODES.map((n, i) => <GraphNode key={n.id} node={n} index={i} />)}
            </svg>
          </div>
          <EvidencePanel />
        </div>
      </div>
      <figcaption className="sr-only">Example investigation in the Cybnetic workbench.</figcaption>
    </figure>
  );
}

function GraphNode({ node, index }: { node: GNode; index: number }) {
  const color = typeColor(node.type);
  const r = node.state === "target" ? 13 : 9;
  const below = node.y >= 60;
  // Clear the selection ring / target halo, not just the dot.
  const gap = node.state === "selected" ? 33 : node.state === "target" ? 30 : r + 14;
  return (
    <g className="graph-node" style={{ "--i": index } as CSSProperties}>
      {node.state === "selected" && <circle cx={node.x} cy={node.y} r={r + 9} fill="none" stroke="rgb(var(--accent))" strokeWidth={1.5} opacity={0.9} />}
      {node.state === "target" && <circle cx={node.x} cy={node.y} r={r + 7} fill="rgb(var(--accent) / 0.14)" />}
      <circle cx={node.x} cy={node.y} r={r} fill={color}
        stroke={node.state === "unverified" ? "rgb(var(--danger))" : "rgb(var(--canvas))"}
        strokeWidth={node.state === "unverified" ? 2 : 3} strokeDasharray={node.state === "unverified" ? "3 3" : undefined} />
      <text x={node.x} y={below ? node.y + gap : node.y - r - 8} textAnchor="middle"
        className="fill-ink font-mono" style={{ fontSize: 11 }}>{node.label}</text>
    </g>
  );
}

function EvidencePanel() {
  const rows: { text: string; source: string; at: string; tone?: "danger" }[] = [
    { text: "Director of Example SRL", source: "company registry", at: "14:02:31" },
    { text: "Owns a.popescu@example-srl.ro", source: "web read", at: "14:03:02" },
    { text: "Same name in Cluj, nothing else shared", source: "social search", at: "14:04:40", tone: "danger" },
  ];
  return (
    <div className="border-t border-line p-5 lg:border-t-0">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: typeColor("Person") }} aria-hidden />
        <span className="text-xs font-medium" style={{ color: typeColor("Person") }}>Person</span>
        <span className="ml-auto rounded-full border border-accent/55 bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent-text">Confirmed</span>
      </div>
      <p className="mt-2 text-lg font-semibold tracking-tight">Ana Popescu</p>
      <p className="text-xs text-muted">Confidence 0.94 from 3 independent findings</p>
      <ul className="mt-4 space-y-2.5">
        {rows.map((r) => (
          <li key={r.text} className={`rounded-lg border px-3 py-2.5 ${
            r.tone === "danger" ? "border-dashed border-danger/70 bg-danger/5" : "border-line bg-ground/60"}`}>
            <p className="text-[13px] leading-snug">{r.text}</p>
            <p className="mt-1 text-xs text-muted">
              <span className="font-mono text-ink/80">{r.source}</span> at {r.at}
              {r.tone === "danger" && <span className="ml-1.5 font-medium text-danger">Unverified match</span>}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- sources --------------------------------------------------------------------------------

const SOURCES = ["DNS & RDAP", "Certificate transparency", "Company registries", "OpenSanctions", "Interpol notices",
  "Have I Been Pwned", "VirusTotal", "urlscan.io", "GitHub", "Social profiles", "Web archives", "Search engines"];

function Sources() {
  return (
    <section aria-labelledby="sources" className="border-b border-line/60">
      <Wrap className="flex flex-col gap-4 py-10 md:flex-row md:items-center md:gap-10">
        <h2 id="sources" className="shrink-0 text-sm font-medium text-muted md:w-48">Collects from 30+ open sources, including</h2>
        <ul className="flex flex-wrap gap-2">
          {SOURCES.map((s) => (
            <li key={s} className="rounded-full border border-line bg-panel/60 px-3 py-1 text-[13px] text-ink/85">{s}</li>
          ))}
        </ul>
      </Wrap>
    </section>
  );
}

// --- how it works ---------------------------------------------------------------------------

const PROCESS: [string, string][] = [
  ["Open it with a purpose", "Every investigation records why it exists. The purpose sits in the audit log and on the report."],
  ["Add targets", "A domain, an email address, a company, a person or a username. Anything active is opt-in."],
  ["Agents collect and verify", "Claude agents follow leads across sources, then a verification pass confirms or rejects each entity."],
  ["Deliver a cited report", "Each claim links to its lookup, retrieval time and content hash. Unverified matches stay labelled."],
];

function Process() {
  return (
    <section id="how" aria-labelledby="how-title" className="scroll-mt-16 border-b border-line/60">
      <Wrap className="py-20 md:py-24">
        <h2 id="how-title" className="max-w-xl text-[32px] font-semibold leading-tight tracking-[-0.02em] sm:text-[40px]">
          From a single name to a report you can sign.
        </h2>
        <ol className="relative mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-8">
          <span aria-hidden className="absolute left-0 right-0 top-[15px] hidden h-px bg-line lg:block" />
          {PROCESS.map(([title, body], i) => (
            <li key={title} className="relative">
              <span className="relative z-10 flex h-8 w-8 items-center justify-center rounded-full border border-accent/60 bg-ground font-mono text-sm text-accent-text">
                {i + 1}
              </span>
              <h3 className="mt-4 text-[17px] font-semibold leading-snug">{title}</h3>
              <p className="mt-2 text-[15px] leading-[1.6] text-muted">{body}</p>
            </li>
          ))}
        </ol>
      </Wrap>
    </section>
  );
}

// --- features (bento) -----------------------------------------------------------------------

function Tile({ icon: Icon, title, body, className = "", children }: {
  icon: LucideIcon; title: string; body: string; className?: string; children?: ReactNode;
}) {
  return (
    <article className={`group flex flex-col overflow-hidden rounded-2xl border border-line bg-panel/70 transition-colors duration-200 hover:border-muted/60 ${className}`}>
      <div className="p-6">
        <Icon className="h-5 w-5 text-accent-text" aria-hidden />
        <h3 className="mt-4 text-[17px] font-semibold">{title}</h3>
        <p className="mt-2 max-w-[52ch] text-[15px] leading-[1.6] text-muted">{body}</p>
      </div>
      {children && <div className="mt-auto px-6 pb-6">{children}</div>}
    </article>
  );
}

function Features() {
  return (
    <section id="features" aria-labelledby="features-title" className="scroll-mt-16 border-b border-line/60">
      <Wrap className="py-20 md:py-24">
        <h2 id="features-title" className="max-w-2xl text-[32px] font-semibold leading-tight tracking-[-0.02em] sm:text-[40px]">
          Everything a team needs to run investigations it can stand behind.
        </h2>
        <div className="mt-12 grid gap-4 md:grid-cols-6">
          <Tile icon={Network} className="md:col-span-4 md:row-span-2" title="A graph that remembers where everything came from"
            body="People, companies, domains, addresses and accounts, joined by how they relate. Open any node to see the findings behind it, merge duplicates, and pivot from there.">
            <div className="graph-surface rounded-xl border border-line p-4">
              <ul className="space-y-2 font-mono text-[12.5px]">
                {[["Domain", "example-srl.ro", "resolves to 203.0.113.24", "dns"],
                  ["Organization", "Example SRL", "registrant of the domain", "rdap"],
                  ["Person", "Ana Popescu", "director of Example SRL", "company registry"]].map(([t, k, how, src]) => (
                  <li key={k} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg bg-panel/80 px-3 py-2">
                    <span style={{ color: typeColor(t) }}>{t}</span>
                    <span className="text-ink">{k}</span>
                    <span className="font-sans text-xs text-muted">{how}, via <span className="font-mono text-ink/80">{src}</span></span>
                  </li>
                ))}
              </ul>
            </div>
          </Tile>
          <Tile icon={Bot} className="md:col-span-2" title="Agents that check their own work"
            body="Claude agents collect in parallel, then a verification pass confirms or rejects each entity against the subject." />
          <Tile icon={ShieldCheck} className="md:col-span-2" title="Unverified stays unverified"
            body="Name-only matches are never promoted on their own. Reports mark them, and an analyst decides." />
          <Tile icon={Users} className="md:col-span-2" title="Workspaces and roles"
            body="Invite colleagues or create their accounts. Each workspace only ever sees its own investigations.">
            <div className="flex flex-wrap gap-1.5">
              {["Owner", "Admin", "Analyst", "Viewer"].map((r) => (
                <span key={r} className="rounded-md border border-line bg-ground/60 px-2 py-0.5 text-xs">{r}</span>
              ))}
            </div>
          </Tile>
          <Tile icon={ScrollText} className="md:col-span-2" title="An audit log nobody can edit"
            body="Who searched for what, when, and under which investigation. Append-only, so it holds up later.">
            <div className="space-y-1 rounded-lg border border-line bg-ground/60 overflow-x-auto whitespace-pre p-2.5 font-mono text-[11.5px] text-muted">
              <p><span className="text-ink/80">14:02:09</span>  ana@example-firm.ro  connector.call dns</p>
              <p><span className="text-ink/80">14:04:40</span>  verification agent  entity.reject</p>
            </div>
          </Tile>
          <Tile icon={TerminalSquare} className="md:col-span-2" title="API and MCP"
            body="Workspace API keys open the REST API and an MCP endpoint for scripts and Claude Code.">
            <code className="block overflow-x-auto whitespace-nowrap rounded-lg border border-line bg-ground/60 px-3 py-2 font-mono text-[11.5px] text-ink/85">
              claude mcp add osint …/mcp
            </code>
          </Tile>
          <Tile icon={FileCheck2} className="md:col-span-6 lg:col-span-6" title="Client and internal reports"
            body="One dossier, two outputs: a client report with captures and citations, and an internal version with every lead and its status. Export to Word or HTML." />
        </div>
      </Wrap>
    </section>
  );
}

// --- pricing --------------------------------------------------------------------------------

const PLANS: { name: string; for: string; features: string[]; featured?: boolean }[] = [
  { name: "Team", for: "For investigation and security teams getting started.", featured: true,
    features: ["Up to 15 seats", "100 open investigations", "Claude agents and every source", "Workspace API keys and MCP", "Audit log and retention controls"] },
  { name: "Enterprise", for: "For organisations with many teams or strict requirements.",
    features: ["Unlimited seats and investigations", "Sign-in through your own identity provider", "Collection from the target's country", "Custom retention and legal hold", "One admin view across all your teams"] },
];

function Pricing({ signedIn }: { signedIn: boolean }) {
  return (
    <section id="pricing" aria-labelledby="pricing-title" className="scroll-mt-16 border-b border-line/60">
      <Wrap className="py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 id="pricing-title" className="text-[32px] font-semibold leading-tight tracking-[-0.02em] sm:text-[40px]">Pricing</h2>
          <p className="mt-3 text-[17px] text-muted">Prices are being finalised. Create an account and we will set up your workspace with you.</p>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-2">
          {PLANS.map((p) => (
            <article key={p.name} className={`relative flex flex-col rounded-2xl border p-7 ${
              p.featured ? "border-accent/60 bg-panel shadow-[0_20px_60px_-30px_rgb(var(--accent)/0.45)]" : "border-line bg-panel/60"}`}>
              <h3 className="text-xl font-semibold">{p.name}</h3>
              <p className="mt-1 text-[15px] text-muted">{p.for}</p>
              <p className="mt-6 flex items-baseline gap-2">
                <span className="text-[44px] font-semibold leading-none tracking-tight">TBD</span>
              </p>
              <ul className="mt-6 space-y-2.5 text-[15px]">
                {p.features.map((f) => (
                  <li key={f} className="flex gap-2.5">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent-text" aria-hidden /> {f}
                  </li>
                ))}
              </ul>
              <div className="mt-8 pt-2">
                <a href={signedIn ? APP : SIGN_UP}
                  className={`btn w-full min-h-[44px] cursor-pointer ${p.featured ? "btn-primary" : ""}`}>
                  {signedIn ? "Open your workspace" : "Create an account"}
                </a>
              </div>
            </article>
          ))}
        </div>
      </Wrap>
    </section>
  );
}

// --- closing --------------------------------------------------------------------------------

function ClosingCta({ signedIn }: { signedIn: boolean }) {
  return (
    <section aria-labelledby="cta-title">
      <Wrap className="py-20 md:py-24">
        <div className="hero-backdrop cta-band overflow-hidden rounded-3xl border border-line bg-panel/40 px-6 py-14 text-center sm:px-12">
          <h2 id="cta-title" className="mx-auto max-w-2xl text-[30px] font-semibold leading-tight tracking-[-0.02em] sm:text-[38px]">
            Your next investigation, with every source on the record.
          </h2>
          <p className="mx-auto mt-4 max-w-[52ch] text-[17px] text-muted">
            Built for authorised work: due diligence, security assessments, fraud and threat investigations.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <PrimaryCta signedIn={signedIn} large />
            {!signedIn && <a href={SIGN_IN} className="btn min-h-[48px] cursor-pointer px-6 text-[15px]">Sign in</a>}
          </div>
        </div>
      </Wrap>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-line/60">
      <Wrap className="flex flex-wrap items-center gap-x-6 gap-y-3 py-8 text-sm text-muted">
        <span className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-5 w-5" /> Team Cybnetic, Craiova Hackathon 2026
        </span>
        <nav aria-label="Footer" className="flex gap-5 sm:ml-auto">
          {NAV.map(([href, label]) => <a key={href} href={href} className="transition-colors hover:text-ink">{label}</a>)}
          <a href={SIGN_IN} className="transition-colors hover:text-ink">Sign in</a>
        </nav>
      </Wrap>
    </footer>
  );
}
