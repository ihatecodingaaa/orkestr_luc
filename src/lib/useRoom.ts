"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getSplit, readPublicSession } from "./orkestr-api";
import type { PublicSession } from "./orkestr-api";
import { demoSplit } from "./split";
import type { MealSplit, SplitMode } from "./split";

// Polls the read-only public snapshot so every device (presenter laptop + each
// friend's phone) converges on the same room state. The endpoint never runs the
// pipeline, so polling is always payment-safe. Errors are non-fatal: the last
// good snapshot is kept and views fall back to local state, so a slow or
// unreachable backend never breaks the demo.
export function usePublicSession(
  sessionId: string | null,
  {
    intervalMs = 2500,
    enabled = true,
    viewerId,
  }: { intervalMs?: number; enabled?: boolean; viewerId?: string } = {},
) {
  const [data, setData] = useState<PublicSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const loggedRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      // viewerId picks the member token so a participant sees their own
      // constraint detail; the presenter's host token unlocks the full view.
      const snap = await readPublicSession(sessionId, viewerId);
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
  }, [sessionId, viewerId]);

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

// Where the split numbers came from — used only for a subtle source-of-truth
// badge in the UI, never to gate any payment logic.
export type SplitSource = "backend" | "local";

// Smart split with a graceful fallback. When `enabled` (live pipeline on + a real
// backend session), it asks the backend /api/split — the calculation-only source
// of truth — and uses that result. On any failure/timeout/offline it silently
// falls back to the local TS calculator (demoSplit), so the demo never dead-ends.
// The backend call is read-only and never triggers a payment.
export function useSplit(
  sessionId: string | null,
  mode: SplitMode,
  { enabled = true, timeoutMs = 6000 }: { enabled?: boolean; timeoutMs?: number } = {},
) {
  const local = useMemo(() => demoSplit(mode), [mode]);
  const [backend, setBackend] = useState<MealSplit | null>(null);
  // Becomes true only once a backend fetch has settled, so the fallback note is
  // never shown while the very first request is still in flight.
  const [attempted, setAttempted] = useState(false);
  const aliveRef = useRef(true);
  const loggedRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    // Not eligible for a backend split — stay on the local calculator. All state
    // updates happen in the async callbacks below (never synchronously here).
    if (!enabled || !sessionId) {
      return () => {
        aliveRef.current = false;
      };
    }
    getSplit(sessionId, mode, timeoutMs)
      .then((result) => {
        if (!aliveRef.current) return;
        setBackend(result);
        setAttempted(true);
        loggedRef.current = false;
      })
      .catch((e) => {
        if (!aliveRef.current) return;
        // Backend split unavailable — fall back to the local calculator. Log the
        // real error once (not the UI); the view stays calm and payment-safe.
        setBackend(null);
        setAttempted(true);
        if (!loggedRef.current) {
          loggedRef.current = true;
          console.warn(`[orkestr] backend split unavailable for ${sessionId}:`, e);
        }
      });
    return () => {
      aliveRef.current = false;
    };
  }, [sessionId, mode, enabled, timeoutMs]);

  const useBackend = enabled && backend !== null;
  const source: SplitSource = useBackend ? "backend" : "local";
  const split = useBackend ? backend : local;
  // True only after a backend attempt settled AND fell back — lets the presenter
  // show a calm note without flashing it during the initial in-flight fetch.
  const fellBack = enabled && Boolean(sessionId) && attempted && backend === null;
  return { split, source, fellBack };
}
