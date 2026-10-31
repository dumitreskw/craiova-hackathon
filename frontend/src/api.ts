// Thin fetch wrapper for /api/v1. The browser sends the HttpOnly session
// cookie (same origin); who acted is decided on the server from the session.
// A 401 means the session ended: go through sign-in and come back here.

const BASE = "/api/v1";

export function signInUrl(): string {
  const back = window.location.pathname + window.location.search + window.location.hash;
  return `/auth/login?next=${encodeURIComponent(back)}`;
}

let redirecting = false;
function onUnauthorized(): void {
  if (redirecting) return;
  redirecting = true;
  window.location.assign(signInUrl());
}

/** An HTTP error with FastAPI's `detail` already pulled out. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(describeDetail(status, detail));
  }
}

function describeDetail(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation: [{loc: [...], msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc || []).filter((p) => p !== "body").join(".");
        return field ? `${field}: ${d.msg}` : String(d.msg);
      })
      .join("\n");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return `Request failed (${status})`;
}

function headers(extra?: HeadersInit): Headers {
  return new Headers(extra);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: headers() };
  if (body instanceof FormData) {
    init.body = body; // the browser sets the multipart boundary
  } else if (body !== undefined) {
    (init.headers as Headers).set("Content-Type", "application/json");
    init.body = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, init);
  if (res.status === 401) onUnauthorized();
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = await res.text().catch(() => null);
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") || "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
  text: (path: string) => request<string>("GET", path),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/**
 * Server-sent events over fetch (EventSource only surfaces unnamed events,
 * and the backend names every event, e.g. "tool.started").
 * Reconnects with backoff until `signal` aborts.
 */
export async function streamEvents(
  path: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  onStatus: (connected: boolean) => void,
  signal: AbortSignal,
): Promise<void> {
  let delay = 1000;
  while (!signal.aborted) {
    try {
      const res = await fetch(BASE + path, { headers: headers({ Accept: "text/event-stream" }), signal });
      if (res.status === 401) onUnauthorized();
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
      onStatus(true);
      delay = 1000;
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value.replace(/\r\n/g, "\n");
        let cut: number;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          let event = "message";
          const lines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
          }
          if (!lines.length) continue;
          try {
            onEvent(event, JSON.parse(lines.join("\n")));
          } catch {
            onEvent(event, { raw: lines.join("\n") });
          }
        }
      }
    } catch {
      if (signal.aborted) return;
    }
    onStatus(false);
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 15000);
  }
}

export async function download(path: string, filename: string): Promise<void> {
  const res = await fetch(BASE + path, { headers: headers() });
  if (res.status === 401) onUnauthorized();
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
