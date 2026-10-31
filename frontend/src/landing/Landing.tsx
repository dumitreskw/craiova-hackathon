import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { typeColor } from "../entityStyle";

const SIGN_UP = "/auth/register?next=/app/";
const SIGN_IN = "/auth/login?next=/app/";
const APP = "/app/";

/** Is there a session already? Then the header offers the workspace instead. */
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
      <Header signedIn={signedIn} />
      <main>
        <Hero signedIn={signedIn} />
        <Process />
        <Capabilities />
        <Plans signedIn={signedIn} />
        <Responsible />
      </main>
      <Footer />
    </div>
  );
}

function Wrap({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-6xl px-4 sm:px-6 ${className}`}>{children}</div>;
}

function Header({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="border-b border-line/70">
      <Wrap className="flex h-16 items-center gap-3">
        <a href="/" className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-[15px] font-semibold tracking-tight">Cybnetic OSINT</span>
        </a>
        <nav className="ml-auto flex items-center gap-2" aria-label="Account">
          {signedIn ? (
            <a href={APP} className="btn btn-primary">Open your workspace</a>
          ) : (
            <>
              <a href={SIGN_IN} className="btn btn-quiet">Sign in</a>
              <a href={SIGN_UP} className="btn btn-primary max-[380px]:hidden">Create account</a>
            </>
          )}
        </nav>
      </Wrap>
    </header>
  );
}

// --- hero --------------------------------------------------------------------------------

interface Step {
  type: string;
  value: string;
  how: string;
  source: string;
  at: string;
  state?: "confirmed" | "unverified";
}

// A fictional company on reserved names and documentation address ranges.
const TRAIL: Step[] = [
  { type: "Domain", value: "example-srl.ro", how: "Target", source: "added by you", at: "14:02:07" },
  { type: "IPAddress", value: "203.0.113.24", how: "resolves to", source: "dns", at: "14:02:09" },
  { type: "Organization", value: "Example SRL", how: "registrant of the domain", source: "rdap", at: "14:02:10" },
  { type: "Person", value: "Ana Popescu", how: "director of Example SRL", source: "company registry", at: "14:02:31",
    state: "confirmed" },
  { type: "Email", value: "a.popescu@example-srl.ro", how: "published on the company site", source: "web read",
    at: "14:03:02" },
  { type: "Person", value: "Ana Popescu, Cluj", how: "same name, nothing else in common", source: "social search",
    at: "14:04:40", state: "unverified" },
];

function Hero({ signedIn }: { signedIn: boolean }) {
  return (
    <section className="border-b border-line/70">
      <Wrap className="grid items-center gap-12 py-16 md:py-24 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div>
          <h1 className="max-w-[15ch] text-[40px] font-semibold leading-[1.05] tracking-[-0.025em] sm:text-[54px]">
            Every fact in the report traces back to where it was found.
          </h1>
          <p className="mt-6 max-w-[56ch] text-[17px] leading-[1.6] text-muted">
            Start from a person, a company or a domain. The workbench queries open sources, pivots on what it finds and
            builds a graph where each entity carries the lookups behind it, so the report you hand over can be checked
            line by line.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            {signedIn ? (
              <a href={APP} className="btn btn-primary min-h-[44px] px-5 text-[15px]">Open your workspace</a>
            ) : (
              <>
                <a href={SIGN_UP} className="btn btn-primary min-h-[44px] px-5 text-[15px]">Create a workspace</a>
                <a href={SIGN_IN} className="btn min-h-[44px] px-5 text-[15px]">Sign in</a>
              </>
            )}
          </div>
          <p className="mt-4 text-sm text-muted">Free for small teams: 3 seats and 5 open investigations.</p>
        </div>
        <Trail />
      </Wrap>
    </section>
  );
}

function Trail() {
  return (
    <figure className="graph-surface relative overflow-hidden rounded-xl border border-line p-5 sm:p-7"
      aria-label="Example: a domain resolved into facts, each with its source and retrieval time">
      <ol className="relative space-y-3.5">
        <span aria-hidden className="trail-line absolute bottom-6 left-[11px] top-6 w-px bg-line" />
        {TRAIL.map((s, i) => (
          <TrailStep key={s.value} step={s} index={i} />
        ))}
      </ol>
      <figcaption className="mt-5 border-t border-line/70 pt-3 text-xs text-muted">
        The second Ana Popescu shares a name and nothing else, so she stays an unverified match until someone confirms
        or rejects her.
      </figcaption>
    </figure>
  );
}

function TrailStep({ step, index }: { step: Step; index: number }) {
  const color = typeColor(step.type);
  const unverified = step.state === "unverified";
  const confirmed = step.state === "confirmed";
  return (
    <li className="trail-step relative flex gap-4" style={{ "--i": index } as CSSProperties}>
      <span aria-hidden className="relative z-10 mt-3 flex h-[23px] w-[23px] shrink-0 items-center justify-center rounded-full border bg-canvas"
        style={{ borderColor: color }}>
        <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      </span>
      <div className={`min-w-0 flex-1 rounded-lg border bg-panel/90 px-3.5 py-2.5 ${
        unverified ? "border-dashed border-danger/70" : confirmed ? "border-accent/60" : "border-line"}`}>
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
          <span className="text-xs font-medium" style={{ color }}>{step.type === "IPAddress" ? "IP address" : step.type}</span>
          <span className="min-w-0 break-all font-mono text-[13.5px] text-ink">{step.value}</span>
          {confirmed && <span className="ml-auto text-xs font-medium text-accent-text">Confirmed</span>}
          {unverified && <span className="ml-auto text-xs font-medium text-danger">Unverified match</span>}
        </div>
        <p className="mt-0.5 text-[13px] text-muted">
          {step.how}
          {index > 0 && (
            <>
              <span className="text-muted/70">, via </span>
              <span className="font-mono text-[12.5px] text-ink/80">{step.source}</span>
              <span className="text-muted/70"> at {step.at}</span>
            </>
          )}
        </p>
      </div>
    </li>
  );
}

// --- how it runs ------------------------------------------------------------------------

const PROCESS: [string, string][] = [
  ["Open it with a purpose",
    "Every investigation records why it exists. The purpose sits in the audit log and on the report, next to who ran each lookup."],
  ["Add targets",
    "A domain, an email address, a company, a person or a username. Passive sources run first; anything that touches the target's own systems is opt-in."],
  ["Let the agents collect and check",
    "Claude agents pivot through DNS and certificates, company registries, sanctions lists, breach data and social profiles, then a verification pass confirms or rejects each entity against the subject."],
  ["Hand over a report that cites itself",
    "Each claim links to the lookup behind it, with the retrieval time and a hash of what came back. Unverified matches stay labelled as unverified."],
];

function Process() {
  return (
    <section className="border-b border-line/70" aria-labelledby="process">
      <Wrap className="py-16 md:py-20">
        <h2 id="process" className="text-[28px] font-semibold tracking-tight">How an investigation runs</h2>
        <ol className="mt-10 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {PROCESS.map(([title, body], i) => (
            <li key={title} className="border-t-2 border-accent/70 pt-4">
              <span className="font-mono text-sm text-accent-text">Step {i + 1}</span>
              <h3 className="mt-2 text-[17px] font-semibold leading-snug">{title}</h3>
              <p className="mt-2 text-[15px] leading-[1.6] text-muted">{body}</p>
            </li>
          ))}
        </ol>
      </Wrap>
    </section>
  );
}

// --- capabilities -------------------------------------------------------------------------

const CAPABILITIES: [string, string][] = [
  ["A graph of everything found",
    "People, companies, domains, addresses and accounts, joined by how they relate. Filter by type, open any node to see the findings that support it, merge duplicates, and pivot from there."],
  ["Workspaces for teams",
    "Invite colleagues with a link or create their accounts yourself. Viewers read, analysts investigate, admins manage people and keys. Each workspace only ever sees its own investigations."],
  ["An audit log nobody can edit",
    "Who searched for what, when, under which investigation, and whether it was allowed. The log is append-only, so it holds up when a client or a court asks."],
  ["The same sources from your tools",
    "Workspace API keys open the REST API and an MCP endpoint, so scripts and Claude Code can run the same lookups under the same audit trail."],
];

function Capabilities() {
  return (
    <section className="border-b border-line/70" aria-labelledby="capabilities">
      <Wrap className="py-16 md:py-20">
        <h2 id="capabilities" className="text-[28px] font-semibold tracking-tight">What a workspace gives you</h2>
        <dl className="mt-8 divide-y divide-line/70 border-y border-line/70">
          {CAPABILITIES.map(([title, body]) => (
            <div key={title} className="grid gap-2 py-6 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:gap-10">
              <dt className="text-[17px] font-semibold">{title}</dt>
              <dd className="max-w-[64ch] text-[15px] leading-[1.6] text-muted">{body}</dd>
            </div>
          ))}
        </dl>
      </Wrap>
    </section>
  );
}

// --- plans -------------------------------------------------------------------------------

const PLAN_ROWS: [string, string, string, string][] = [
  ["Seats", "3", "15", "No limit"],
  ["Open investigations", "5", "100", "No limit"],
  ["API keys", "2", "20", "No limit"],
  ["Claude agents and every source", "Included", "Included", "Included"],
];

function Plans({ signedIn }: { signedIn: boolean }) {
  return (
    <section className="border-b border-line/70" aria-labelledby="plans">
      <Wrap className="py-16 md:py-20">
        <h2 id="plans" className="text-[28px] font-semibold tracking-tight">Plans</h2>
        <p className="mt-2 max-w-[60ch] text-[15px] text-muted">
          Every workspace starts on Free. Write to us when your team outgrows it and we move you up the same day.
        </p>
        <div className="mt-8 overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-left text-[15px]">
            <thead>
              <tr className="border-b border-line">
                <th className="py-3 pr-4 font-normal text-muted"><span className="sr-only">Limit</span></th>
                <th className="py-3 pr-4 text-[17px] font-semibold">Free</th>
                <th className="py-3 pr-4 text-[17px] font-semibold">Team</th>
                <th className="py-3 text-[17px] font-semibold">Enterprise</th>
              </tr>
            </thead>
            <tbody>
              {PLAN_ROWS.map(([label, ...cells]) => (
                <tr key={label} className="border-b border-line/60">
                  <th scope="row" className="py-3 pr-4 font-normal text-muted">{label}</th>
                  {cells.map((c, i) => <td key={i} className="py-3 pr-4 font-medium">{c}</td>)}
                </tr>
              ))}
              <tr>
                <td className="pt-5" />
                <td className="pr-4 pt-5">
                  <a href={signedIn ? APP : SIGN_UP} className="btn btn-primary">
                    {signedIn ? "Open your workspace" : "Create a workspace"}
                  </a>
                </td>
                <td className="pr-4 pt-5 text-sm text-muted" colSpan={2}>Team and Enterprise are set up on request.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Wrap>
    </section>
  );
}

function Responsible() {
  return (
    <section aria-labelledby="use">
      <Wrap className="grid gap-4 py-16 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:gap-10 md:py-20">
        <h2 id="use" className="text-[22px] font-semibold tracking-tight">Built for authorised work</h2>
        <p className="max-w-[64ch] text-[15px] leading-[1.6] text-muted">
          Due diligence, security assessments, fraud and threat investigations. Collection is passive by default, every
          lookup is attributed to a named account, and evidence expires on a retention schedule unless an investigation
          is placed on legal hold.
        </p>
      </Wrap>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-line/70">
      <Wrap className="flex flex-wrap items-center gap-x-6 gap-y-2 py-8 text-sm text-muted">
        <span>Team Cybnetic, Craiova Hackathon 2026</span>
        <a href={SIGN_IN} className="ml-auto hover:text-ink">Sign in</a>
        <a href={SIGN_UP} className="hover:text-ink">Create account</a>
      </Wrap>
    </footer>
  );
}
