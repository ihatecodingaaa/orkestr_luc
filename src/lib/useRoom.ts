"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { readPublicSession } from "./orkestr-api";
import type { PublicSession } from "./orkestr-api";

// Polls the read-only public snapshot so every device (presenter laptop + each
// friend's phone) converges on the same room state. The endpoint never runs the
// pipeline, so polling is always payment-safe. Errors are non-fatal: the last
// good snapshot is kept and views fall back to local state, so a slow or
// unreachable backend never breaks the demo.
export function usePublicSession(
  sessionId: string | null,
  { intervalMs = 2500, enabled = true }: { intervalMs?: number; enabled?: boolean } = {},
) {
  const [data, setData] = useState<PublicSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const loggedRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      const snap = await readPublicSession(sessionId);
      if (!aliveRef.current) return;
      setData(snap);
      setError(null);
      loggedRef.current = false;
    } catch (e) {
      if (!aliveRef.current) return;
      setError(e instanceof Error ? e.message : "offline");
      // Surface the real error to the console once per outage (not every poll),
      // so it is never hidden — the UI itself stays calm and falls back.
      if (!loggedRef.current) {
        loggedRef.current = true;
        console.warn(`[orkestr] room sync unavailable for ${sessionId}:`, e);
      }
    }
  }, [sessionId]);

  useEffect(() => {
    aliveRef.current = true;
    if (!sessionId || !enabled) return;
    // First poll is deferred (not a synchronous setState in the effect body);
    // subsequent polls run from the interval callback.
    const kick = window.setTimeout(() => void refresh(), 0);
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => {
      aliveRef.current = false;
      window.clearTimeout(kick);
      window.clearInterval(id);
    };
  }, [sessionId, enabled, intervalMs, refresh]);

  return { data, error, refresh };
}
