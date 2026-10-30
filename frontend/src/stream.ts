import { useEffect, useRef, useState } from "react";
import { streamEvents } from "./api";
import type { StreamEvent } from "./types";

type Listener = (e: StreamEvent) => void;

const MAX_EVENTS = 600;

/**
 * One SSE connection per open investigation. Components either read the
 * rolling `events` list (activity feed, transcripts) or `subscribe` to apply
 * each event as it arrives (the graph).
 */
export function useInvestigationStream(id: string) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    const ctrl = new AbortController();
    setEvents([]);
    streamEvents(
      `/investigations/${id}/stream`,
      (event, data) => {
        const e: StreamEvent = { event, data, at: Date.now() };
        listeners.current.forEach((l) => l(e));
        if (event === "hello") return;
        setEvents((prev) => {
          const next = prev.length >= MAX_EVENTS ? prev.slice(prev.length - MAX_EVENTS + 1) : prev.slice();
          next.push(e);
          return next;
        });
      },
      setConnected,
      ctrl.signal,
    );
    return () => ctrl.abort();
  }, [id]);

  function subscribe(l: Listener): () => void {
    listeners.current.add(l);
    return () => {
      listeners.current.delete(l);
    };
  }

  return { events, connected, subscribe };
}

export type InvestigationStream = ReturnType<typeof useInvestigationStream>;
