"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GroupRoomHeader } from "./GroupRoomHeader";
import { MemberConstraintCard } from "./MemberConstraintCard";
import { WaitingRoom } from "./WaitingRoom";
import { AgentTimeline } from "./AgentTimeline";
import { FinalPlanCard } from "./FinalPlanCard";
import { PaymentProofCard } from "./PaymentProofCard";
import { SmartSplitCard } from "./SmartSplitCard";
import { TrustPanel } from "./TrustPanel";
import { MerchantAdapterCard } from "./MerchantAdapterCard";
import { LiveReadinessPanel } from "./LiveReadinessPanel";
import { LiveUnavailableCard } from "./LiveUnavailableCard";
import { LiveFailureCard } from "./LiveFailureCard";
import { NoVerifiedVenueCard } from "./verification";
import { DemoControls } from "./DemoControls";
import type { DemoFriend, DemoSection } from "./DemoControls";
import { InvitePanel } from "./InvitePanel";
import type { ActivityItem } from "./ActivityFeed";
import type { PillKind } from "./primitives";
import {
  DEMO_PLAN,
  PERSONAS,
  SAVED_RECEIPT,
  SEED_CHAT,
  TIMELINE_STEPS,
  emptyConstraints,
  isReadyToSubmit,
  localRoomId,
  personaById,
  personName,
  presenterLink,
} from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import {
  API_BASE,
  createLiveSession,
  getDemoReceiptStatus,
  getLatestReceipt,
  getStatus,
  markReady,
  paymentMode,
  readLiveState,
  shortHash,
  submitConstraints,
  toReceipt,
} from "@/lib/orkestr-api";
import type {
  Constraints,
  DemoReceiptStatus,
  LiveState,
  Member,
  Plan,
  PublicMember,
  VenueVerification,
  X402Receipt,
} from "@/lib/orkestr-api";
import { applyMemberBudgets } from "@/lib/split";
import type { SplitMode } from "@/lib/split";
import { usePublicSession, useSplit } from "@/lib/useRoom";
import {
  ENABLE_LIVE_PIPELINE,
  IS_LIVE_MODE,
  MODE_COPY,
  PRESENTER_TOOLS_VISIBLE,
} from "@/lib/appMode";
import s from "./live.module.css";

const PERSONA_MEMBERS: Member[] = PERSONAS.map((p) => ({ id: p.id, name: p.name }));

// A venue may be shown as the final plan in Live Mode only when it is genuinely
// grounded (Google Places verified or a real web source). Sample/demo/unverified
// venues are never presented as the live result.
function isRealVenue(plan?: Plan | null): boolean {
  const st = plan?.venue?.verification?.verification_status;
  return st === "verified" || st === "source_only";
}

function bookingModeLabel(v?: VenueVerification): string {
  switch (v?.verification_status) {
    case "verified":
      return "Real handoff · Google Maps";
    case "source_only":
      return "Web source handoff";
    case "demo":
      return "Sample (demo only)";
    default:
      return "Manual / unverified";
  }
}

function seedActivity(): ActivityItem[] {
  return SEED_CHAT.map((c, i) => ({ id: `seed-${i}`, kind: "chat", who: c.who, text: c.text }));
}

function cloneConstraints(c: Constraints): Constraints {
  return { ...c, days: [...c.days], dietary: [...c.dietary] };
}

function memberForms(members: Member[], usePersonaDefaults = false): Record<string, Constraints> {
  return Object.fromEntries(
    members.map((m) => {
      const demo = usePersonaDefaults ? personaById(m.id)?.demo : undefined;
      return [m.id, demo ? cloneConstraints(demo) : emptyConstraints()];
    }),
  );
}

// A friend who submitted from their own phone shows up here via polling — turn
// their public snapshot back into the same Constraints shape the cards render.
function constraintsFromPublic(pm?: PublicMember): Constraints | undefined {
  if (!pm?.constraints) return undefined;
  return {
    days: pm.constraints.available_days ?? [],
    budget: pm.constraints.budget_max != null ? String(pm.constraints.budget_max) : "",
    dietary: pm.constraints.dietary ?? [],
    freeform: pm.freeform ?? "",
  };
}

function SafetyNote({ realX402 }: { realX402: boolean }) {
  if (realX402) {
    return (
      <div className={`${s.safety} ${s.safetyWarn}`} role="alert">
        <span className={s.safetyDot} aria-hidden />
        <div className={s.safetyText}>
          <strong>Live x402 payment detected</strong>
          <span>Check ENABLE_LIVE_REAL_X402 before continuing — normal demos stay mock-safe.</span>
        </div>
      </div>
    );
  }
  return (
    <div className={`${s.safety} ${s.safetyOk}`}>
      <span className={s.safetyDot} aria-hidden />
      <div className={s.safetyText}>
        <strong>{MODE_COPY.safetyOkTitle}</strong>
        <span>{MODE_COPY.safetyOkBody}</span>
      </div>
    </div>
  );
}

function HowToDemo() {
  const [open, setOpen] = useState(false);
  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>How to demo this</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <ol className={s.howtoList}>
          <li>Open this command center on your laptop or projector.</li>
          <li>Share the invite links — or let friends scan their QR cards.</li>
          <li>Friends submit constraints on their phones.</li>
          <li>No phones around? Use Demo Cast to simulate replies.</li>
          <li>Watch the agents discover venues, negotiate, and book.</li>
          <li>Reveal the x402 USDC payment proof — the WOW moment.</li>
        </ol>
      ) : null}
    </div>
  );
}

// Presenter-only, collapsed by default. Shows PUBLIC chain details only — there
// is no wallet connect anywhere in Orkestr, and friends never link crypto.
// Reflects the active receipt (saved fallback or a fresh proof) so the payment
// rail shows the same tx the receipt card does.
function WalletLab({ receipt }: { receipt: X402Receipt }) {
  const [open, setOpen] = useState(false);
  const p = receipt;
  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>How the agent paid · payment rails</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.walletLab}>
          <p className={s.walletLabIntro}>
            The public payment rail, for anyone curious. Friends never hold or connect a wallet —
            Orkestr&apos;s agent wallet pays the merchant wallet once, then the group splits
            internally.
          </p>
          <div className={s.walletLabGrid}>
            <div className={s.walletLabRow}>
              <span className={s.walletLabLabel}>Orkestr Agent Wallet</span>
              <code className={s.walletLabValue}>{p.buyer}</code>
            </div>
            <div className={s.walletLabRow}>
              <span className={s.walletLabLabel}>Merchant Wallet</span>
              <code className={s.walletLabValue}>{p.merchant}</code>
            </div>
            <div className={s.walletLabRow}>
              <span className={s.walletLabLabel}>Asset</span>
              <span className={s.walletLabValue}>{p.network} USDC</span>
            </div>
            <div className={s.walletLabRow}>
              <span className={s.walletLabLabel}>Amount</span>
              <span className={s.walletLabValue}>{p.amount}</span>
            </div>
            <div className={s.walletLabRow}>
              <span className={s.walletLabLabel}>Verified tx</span>
              <span className={s.walletLabValue}>
                <a href={p.basescan} target="_blank" rel="noreferrer">
                  {shortHash(p.txHash, 10, 8)} ↗
                </a>
              </span>
            </div>
          </div>
          <p className={s.walletLabFoot}>Read-only — opening this panel never starts a payment.</p>
        </div>
      ) : null}
    </div>
  );
}

export function GroupRoom({
  group,
  onBack,
  boundRoomId,
}: {
  group: DemoGroup;
  onBack: () => void;
  boundRoomId?: string;
}) {
  const isLive = group.mode === "live";
  const initialMembers = isLive ? [] : PERSONA_MEMBERS;

  const [sessionId, setSessionId] = useState<string | null>(boundRoomId ?? null);
  const [members, setMembers] = useState<Member[]>(initialMembers);
  const [forms, setForms] = useState<Record<string, Constraints>>(() =>
    memberForms(initialMembers, !isLive),
  );
  const [submitted, setSubmitted] = useState<Record<string, boolean>>(() =>
    isLive ? {} : Object.fromEntries(PERSONA_MEMBERS.map((m) => [m.id, true])),
  );
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});

  const [state, setState] = useState<LiveState | null>(null);
  const [working, setWorking] = useState(false);
  const [timelineStep, setTimelineStep] = useState(0);
  const [revealed, setRevealed] = useState(!isLive);
  // After a failure, returning to the room to edit constraints must NOT let the
  // auto-run effect immediately recompute the SAME failing constraints in a loop.
  // Set true when the presenter goes back to edit; cleared on the next submit/retry.
  const [suppressAutoRun, setSuppressAutoRun] = useState(false);
  // Soft, demo-safe sync state — we never surface raw backend errors to the UI.
  // Real errors still go to the console (see usePublicSession + the catches).
  const [syncWarn, setSyncWarn] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  // Backend couldn't mint a room — we run on a local id so links + reveal still
  // work. When true we stop polling /public (it would only 404) and skip writes.
  const [localRoom, setLocalRoom] = useState(false);

  const [nudged, setNudged] = useState<string[]>([]);
  const [activity, setActivity] = useState<ActivityItem[]>(seedActivity);
  const [busy, setBusy] = useState(false);
  const [autoRunning, setAutoRunning] = useState(false);
  // Presenter split control — itemized by default (the whole point of Part 2).
  const [splitMode, setSplitMode] = useState<SplitMode>("itemized");
  // Active x402 receipt shown by the proof card / trust panel / wallet lab.
  // Defaults to the saved verified fallback; a deliberate presenter "fresh proof"
  // run swaps in the new on-chain receipt. Reading /latest on mount NEVER spends.
  const [receipt, setReceipt] = useState<X402Receipt>(SAVED_RECEIPT);
  // Live Mode only: when the backend can't be reached we BLOCK with a real error
  // (LiveUnavailableCard) instead of silently dropping to a local sample room.
  const [backendDown, setBackendDown] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // Public, non-secret fresh-proof status — powers the Live readiness panel and
  // the "payment not run yet" copy. Reading it NEVER triggers a payment.
  const [freshStatus, setFreshStatus] = useState<DemoReceiptStatus | null>(null);
  // "Plan another dinner with this crew": clone-in-progress + inline error state.
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);

  const submittedRef = useRef<Set<string>>(new Set());
  const startedRef = useRef(false);
  const autoRunRef = useRef(false);
  const readyIdsRef = useRef<Set<string>>(new Set());
  const stageRef = useRef<HTMLDivElement>(null);
  const prevStageRef = useRef<"social" | "working" | "final">("social");

  // Cross-device sync: reflect friends who submit from their own phones.
  // Skip polling a local-only room — the backend has no record of it.
  const pub = usePublicSession(sessionId, {
    enabled: isLive && Boolean(sessionId) && !localRoom,
  });
  const backendReady = useMemo(() => new Set(pub.data?.ready ?? []), [pub.data]);
  const pubById = useMemo(() => {
    const map: Record<string, PublicMember> = {};
    (pub.data?.members ?? []).forEach((m) => {
      map[m.id] = m;
    });
    return map;
  }, [pub.data]);

  const pushActivity = useCallback((item: Omit<ActivityItem, "id">) => {
    setActivity((prev) => [
      ...prev,
      { ...item, id: `${item.kind}-${Date.now()}-${Math.round(Math.random() * 1e6)}` },
    ]);
  }, []);

  const runPipeline = useCallback(
    async (sid: string) => {
      if (startedRef.current) return;
      startedRef.current = true;
      setWorking(true);
      setTimelineStep(1);
      pushActivity({ kind: "system", text: "Everyone is in — agents started negotiating" });
      try {
        if (ENABLE_LIVE_PIPELINE) {
          await getStatus(sid);
          const live = await readLiveState(sid);
          setState(live);
        } else {
          await new Promise((resolve) => window.setTimeout(resolve, 5200));
          setState(null);
        }
        setTimelineStep(TIMELINE_STEPS.length);
        setRevealed(true);
      } catch (e) {
        // Live pipeline unavailable — log the real error, then continue with the
        // verified sample result so the demo never dead-ends on stage.
        console.warn("[orkestr] live pipeline unavailable — showing sample result", e);
        setSyncWarn(true);
        setTimelineStep(TIMELINE_STEPS.length);
        setRevealed(true);
      } finally {
        setWorking(false);
      }
    },
    [pushActivity],
  );

  // Bind to a shared room id, or create a fresh live session and make the
  // presenter URL shareable so friends' links resolve to the same backend room.
  // Demo Mode falls back to a local sample room if the backend is unreachable so
  // the demo never dead-ends. Live Mode does NOT — it blocks with a real error.
  const bindLiveSession = useCallback(async () => {
    if (boundRoomId) {
      setSessionId(boundRoomId);
      setMembers(PERSONA_MEMBERS);
      setForms(memberForms(PERSONA_MEMBERS));
      return;
    }
    try {
      const live = await createLiveSession();
      const ms = live.members?.length ? live.members : PERSONA_MEMBERS;
      setSessionId(live.session_id);
      setMembers(ms);
      setForms(memberForms(ms));
      setBackendDown(false);
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", presenterLink(live.session_id));
      }
    } catch (e) {
      console.warn("[orkestr] live room unavailable", e);
      if (IS_LIVE_MODE) {
        // Live Mode: block with a real error — never a fake local room.
        setBackendDown(true);
        return;
      }
      // Demo Mode: fall back to a fully local demo room so the presenter can
      // still run Demo Cast, share invite links, and reach the reveal.
      const localId = localRoomId();
      setSessionId(localId);
      setLocalRoom(true);
      setMembers(PERSONA_MEMBERS);
      setForms(memberForms(PERSONA_MEMBERS));
      setSyncWarn(true);
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", presenterLink(localId));
      }
    }
  }, [boundRoomId]);

  useEffect(() => {
    if (!isLive) return;
    // Deferred so the initial setState never runs synchronously in the effect body.
    const kick = window.setTimeout(() => void bindLiveSession(), 0);
    return () => window.clearTimeout(kick);
  }, [isLive, bindLiveSession]);

  // Live Mode: allow the presenter to retry a failed backend connection.
  const retryLive = useCallback(async () => {
    setRetrying(true);
    try {
      await bindLiveSession();
      await pub.refresh();
    } finally {
      setRetrying(false);
    }
  }, [bindLiveSession, pub]);

  // Public fresh-proof status for the Live readiness panel + "not run yet" copy.
  useEffect(() => {
    if (!isLive) return;
    const kick = window.setTimeout(() => {
      getDemoReceiptStatus()
        .then(setFreshStatus)
        .catch(() => setFreshStatus(null));
    }, 0);
    return () => window.clearTimeout(kick);
  }, [isLive]);

  // On mount, ask the backend for the latest receipt (read-only — NEVER spends).
  // If a fresh proof already exists this process (e.g. presenter ran it, then
  // refreshed), show it; otherwise the saved fallback default stands.
  useEffect(() => {
    if (!isLive) return;
    let active = true;
    getLatestReceipt()
      .then((r) => {
        if (active && r && r.isFresh) setReceipt(r);
      })
      .catch(() => {
        /* backend offline or endpoint absent — keep the saved fallback receipt */
      });
    return () => {
      active = false;
    };
  }, [isLive]);

  const roomMembers = members.length ? members : PERSONA_MEMBERS;

  const isMemberReady = useCallback(
    (id: string) => Boolean(submitted[id]) || backendReady.has(id),
    [submitted, backendReady],
  );

  const readyIds = useMemo(
    () => roomMembers.filter((m) => isMemberReady(m.id)).map((m) => m.id),
    [roomMembers, isMemberReady],
  );
  useEffect(() => {
    readyIdsRef.current = new Set(readyIds);
  }, [readyIds]);

  const allSubmitted = roomMembers.length > 0 && roomMembers.every((m) => isMemberReady(m.id));

  const submitMember = useCallback(
    async (member: Member, override?: Constraints) => {
      const c = override ?? forms[member.id];
      if (!c || !isReadyToSubmit(c)) return;
      // A fresh submit means constraints may have changed — re-enable auto-run so a
      // post-failure edit can recompute once everyone is ready again.
      setSuppressAutoRun(false);
      setSubmitting((p) => ({ ...p, [member.id]: true }));
      if (override) setForms((p) => ({ ...p, [member.id]: c }));
      try {
        // Local-only room: the backend has no record of it, so skip the writes
        // and advance optimistically (nothing to sync, nothing to spam).
        if (sessionId && !localRoom) {
          await submitConstraints(sessionId, member, c);
          await markReady(sessionId, member.id);
        }
      } catch (e) {
        // Could not reach the backend — keep the real error in the console and
        // mark the friend in locally so the demo still advances.
        console.warn(`[orkestr] could not sync ${member.name}'s constraints — keeping local`, e);
        setSyncWarn(true);
      } finally {
        // Optimistic: the friend is in whether or not the backend synced.
        setSubmitted((p) => ({ ...p, [member.id]: true }));
        submittedRef.current.add(member.id);
        setSubmitting((p) => ({ ...p, [member.id]: false }));
      }
      pushActivity({ kind: "system", text: `${member.name} submitted their constraints` });
    },
    [sessionId, localRoom, forms, pushActivity],
  );

  // Single canonical reveal trigger: fire once everyone (real or simulated) is
  // in. Readiness can change from an external source (a friend submitting from
  // their phone arrives via polling), so this reacts in an effect — deferred off
  // the synchronous effect body, and guarded by startedRef against re-entry.
  useEffect(() => {
    if (!isLive || !sessionId || startedRef.current || suppressAutoRun) return;
    if (roomMembers.length === 0 || readyIds.length < roomMembers.length) return;
    const t = window.setTimeout(() => void runPipeline(sessionId), 0);
    return () => window.clearTimeout(t);
  }, [isLive, sessionId, roomMembers.length, readyIds.length, runPipeline, suppressAutoRun]);

  const editMember = useCallback((id: string) => {
    setSubmitted((p) => ({ ...p, [id]: false }));
    submittedRef.current.delete(id);
  }, []);

  const readyAll = useCallback(async () => {
    setBusy(true);
    try {
      await Promise.all(roomMembers.map((m) => submitMember(m, personaById(m.id)?.demo)));
    } finally {
      setBusy(false);
    }
  }, [roomMembers, submitMember]);

  const submitThree = useCallback(async () => {
    setBusy(true);
    try {
      await Promise.all(
        roomMembers.slice(0, 3).map((m) => submitMember(m, personaById(m.id)?.demo)),
      );
    } finally {
      setBusy(false);
    }
  }, [roomMembers, submitMember]);

  // Auto-run: friends trickle in one at a time, so the readiness meter visibly
  // climbs 1/5 → 5/5 just like real phones replying.
  const autoRun = useCallback(async () => {
    if (autoRunRef.current) return;
    autoRunRef.current = true;
    setAutoRunning(true);
    try {
      for (const m of roomMembers) {
        if (readyIdsRef.current.has(m.id)) continue;
        await submitMember(m, personaById(m.id)?.demo);
        await new Promise((resolve) => window.setTimeout(resolve, 1200 + Math.random() * 600));
      }
    } finally {
      autoRunRef.current = false;
      setAutoRunning(false);
    }
  }, [roomMembers, submitMember]);

  const nudge = useCallback(
    (id: string) => {
      setNudged((p) => (p.includes(id) ? p : [...p, id]));
      pushActivity({ kind: "nudge", text: `You nudged ${personName(id)}` });
      window.setTimeout(
        () => pushActivity({ kind: "chat", who: id, text: "On it — picking now! 🙌" }),
        1200,
      );
    },
    [pushActivity],
  );

  // Presenter jump shortcuts (Agent workbench / Reveal proof) can skip past the
  // squad submitting. Seed every member as ready with their persona preset so a
  // revealed plan is never shown behind empty forms — readiness reads 5/5 and
  // cards render their completed state. Local/demo only: no backend writes, and
  // it preserves any real constraints a friend already submitted (natural flow
  // and phone submissions are untouched).
  const seedAllReady = useCallback(() => {
    setForms((prev) => {
      const next = { ...prev };
      for (const m of roomMembers) {
        const current = prev[m.id];
        if (current && isReadyToSubmit(current)) continue; // keep real input
        const demo = personaById(m.id)?.demo;
        next[m.id] = demo ? cloneConstraints(demo) : (current ?? emptyConstraints());
      }
      return next;
    });
    setSubmitted(Object.fromEntries(roomMembers.map((m) => [m.id, true])));
    roomMembers.forEach((m) => submittedRef.current.add(m.id));
  }, [roomMembers]);

  const jumpToWorking = useCallback(() => {
    if (revealed) return;
    seedAllReady();
    // Presenter is driving manually — block the readiness effect from also
    // auto-firing the pipeline once seeding pushes readiness to 5/5.
    startedRef.current = true;
    setWorking(true);
    setTimelineStep((p) => (p < 1 ? 1 : p));
    pushActivity({ kind: "system", text: "Skipped ahead to the agent workbench" });
  }, [revealed, seedAllReady, pushActivity]);

  const revealProof = useCallback(() => {
    seedAllReady();
    startedRef.current = true;
    setWorking(false);
    setTimelineStep(TIMELINE_STEPS.length);
    setRevealed(true);
  }, [seedAllReady]);

  // Failure recovery (P1.2). "Try again" re-runs the pipeline with the current
  // constraints; the backend recomputes and either succeeds or fails again cleanly.
  const retryPlan = useCallback(() => {
    if (!sessionId) return;
    setSuppressAutoRun(false);
    setState(null);
    setRevealed(false);
    setWorking(false);
    setTimelineStep(0);
    startedRef.current = false;
    void runPipeline(sessionId);
  }, [sessionId, runPipeline]);

  // "Edit constraints" / "Relax" / "Back to waiting" return to the editable room.
  // suppressAutoRun stops the readiness effect from instantly recomputing the same
  // failing constraints; it clears the moment a member re-submits (see submitMember).
  const backToRoom = useCallback(() => {
    setSuppressAutoRun(true);
    setState(null);
    setRevealed(false);
    setWorking(false);
    setTimelineStep(0);
    startedRef.current = false;
  }, []);

  const resetDemo = useCallback(async () => {
    setBusy(true);
    setState(null);
    setWorking(false);
    setTimelineStep(0);
    setRevealed(false);
    setSubmitted({});
    setSubmitting({});
    setNudged([]);
    setActivity(seedActivity());
    setSyncWarn(false);
    setNoticeDismissed(false);
    setLocalRoom(false);
    submittedRef.current = new Set();
    startedRef.current = false;
    autoRunRef.current = false;
    setAutoRunning(false);
    try {
      const live = await createLiveSession();
      const ms = live.members?.length ? live.members : PERSONA_MEMBERS;
      setSessionId(live.session_id);
      setMembers(ms);
      setForms(memberForms(ms));
      setBackendDown(false);
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", presenterLink(live.session_id));
      }
    } catch (e) {
      console.warn("[orkestr] reset could not reach the backend", e);
      if (IS_LIVE_MODE) {
        // Live Mode: block with a real error rather than a fake local room.
        setBackendDown(true);
        setBusy(false);
        return;
      }
      // Demo Mode: stay usable on a local demo room.
      const localId = localRoomId();
      setSessionId(localId);
      setLocalRoom(true);
      setMembers(PERSONA_MEMBERS);
      setForms(memberForms(PERSONA_MEMBERS));
      setSyncWarn(true);
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", presenterLink(localId));
      }
    } finally {
      setBusy(false);
    }
  }, []);

  // "Friday coming up?" — clone THIS crew into a fresh session and jump to it.
  // Copies only the member id+name list (constraints come from group memory when
  // they re-submit). Never for ORK-001. On success we do a full navigation to the
  // new room (page.tsx boots it from ?room=); on failure we surface a calm inline
  // note and never crash the settled screen.
  const planAnother = useCallback(async () => {
    if (!sessionId || sessionId === "ORK-001" || cloning) return;
    setCloning(true);
    setCloneError(null);
    try {
      const res = await fetch(`${API_BASE}/api/session/clone/${sessionId}`, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as { session_id?: string };
      if (!data.session_id) throw new Error("clone returned no session_id");
      // Full navigation to the fresh cloned room — no manual state reset needed.
      window.location.href = presenterLink(data.session_id);
    } catch (e) {
      console.warn("[orkestr] could not clone session", e);
      setCloneError("Couldn't start a new plan just now — try again in a moment.");
      setCloning(false);
    }
  }, [sessionId, cloning]);

  // Make the blocking call feel alive: advance the visible step until data lands.
  useEffect(() => {
    if (!working) return;
    const id = window.setInterval(() => {
      setTimelineStep((prev) => (prev < TIMELINE_STEPS.length - 1 ? prev + 1 : prev));
    }, 1100);
    return () => window.clearInterval(id);
  }, [working]);

  const finalReady = revealed || Boolean(state) || !isLive;
  const stage: "social" | "working" | "final" = finalReady
    ? "final"
    : working
      ? "working"
      : "social";

  // Guide the presenter forward: when the story advances off the social stage,
  // bring the agent workbench / final plan into view. Only on forward
  // transitions from social, and honoring reduced-motion.
  useEffect(() => {
    const prev = prevStageRef.current;
    if (stage === prev) return;
    prevStageRef.current = stage;
    if (!isLive || prev !== "social" || stage === "social") return;
    const reduce =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    stageRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [stage, isLive]);

  // The real chosen plan comes from the live pipeline (presenter) or the public
  // snapshot (both are real backend data). Demo Mode falls back to the sample
  // plan so the demo always renders; Live Mode NEVER shows the sample as the
  // result — no real plan means the "no verified restaurant" state instead.
  const realPlan: Plan | null =
    state?.plan ?? (pub.data?.plan ? (pub.data.plan as unknown as Plan) : null);
  const displayPlan: Plan | null = IS_LIVE_MODE ? realPlan : (state?.plan ?? DEMO_PLAN);

  // Backend is the source of truth when the live pipeline is on and a real
  // backend session exists; otherwise the local TS calculator (offline/demo).
  // A local-only room has no backend record, so we never ask /api/split for it.
  const splitLive = ENABLE_LIVE_PIPELINE && isLive && Boolean(sessionId) && !localRoom;
  const {
    split: rawSplit,
    source: splitSource,
    fellBack: splitFellBack,
  } = useSplit(sessionId, splitMode, { enabled: splitLive });

  // Over-budget flags must compare each member's total against THEIR OWN stated
  // budget_max — not a hardcoded demo budget. Build a real budgets-by-id map from
  // the public snapshot (authoritative) with the locally-entered form as fallback,
  // then re-apply it to whatever split we display (backend or local calculator).
  // Only finite, > 0 budgets are included; missing/zero/NaN members are omitted so
  // we never raise a false flag.
  const memberBudgets = useMemo(() => {
    const map: Record<string, number> = {};
    const add = (id: string | undefined, raw: unknown) => {
      if (!id || id in map) return;
      const n = Number(raw);
      if (Number.isFinite(n) && n > 0) map[id] = n;
    };
    (pub.data?.members ?? []).forEach((m) => add(m.id, m.constraints?.budget_max));
    roomMembers.forEach((m) => add(m.id, forms[m.id]?.budget));
    return map;
  }, [pub.data, roomMembers, forms]);

  const split = useMemo(
    () => applyMemberBudgets(rawSplit, memberBudgets),
    [rawSplit, memberBudgets],
  );

  // When to show the "no verified restaurant" state instead of a plan:
  //  • Live Mode: whenever the final plan isn't a genuinely grounded venue.
  //  • Demo Mode: only when strict grounding explicitly returned no venue.
  const venueStatus = state?.venueStatus ?? pub.data?.venue_status ?? null;
  const noVenue = isLive && (
    IS_LIVE_MODE
      ? stage === "final" && !isRealVenue(displayPlan)
      : venueStatus === "no_verified_venue"
  );

  // A genuine backend failure (quorum couldn't agree / no verified venue / mandate
  // integrity block). When true we show the polished failure state instead of a
  // plan — in live mode NEVER a sample venue and NEVER a stale payment proof.
  const failed = Boolean(
    state?.phase === "failed" ||
      state?.bookingFailed ||
      state?.failReason ||
      pub.data?.phase === "failed" ||
      pub.data?.booking_failed,
  );
  const failReason = state?.failReason ?? pub.data?.fail_reason ?? null;

  const planVerification = displayPlan?.venue?.verification;
  const bookingMode = bookingModeLabel(planVerification);

  // A real fresh receipt auto-fired by the backend at booking time (compute_plan /
  // generate_real_plan) arrives on the public snapshot. Prefer it over the saved
  // fallback / presenter state so Live Mode shows a real on-chain tx with no manual
  // trigger. The presenter FreshProofPanel path still works via the `receipt` state
  // that activeReceipt falls back to when the session hasn't auto-fired one.
  const sessionReceipt = useMemo(
    () => (pub.data?.x402_receipt ? toReceipt(pub.data.x402_receipt) : null),
    [pub.data],
  );
  const activeReceipt: X402Receipt = sessionReceipt?.isFresh ? sessionReceipt : receipt;

  // Live Mode: the saved receipt is NOT the live proof — until a fresh proof lands
  // (auto-fired by the booking, or run by the presenter) the card shows "not run
  // yet" with the saved receipt as secondary. Once a real fresh receipt exists,
  // activeReceipt.isFresh is true and the card shows the real payment automatically.
  const awaitingFreshProof = IS_LIVE_MODE && !activeReceipt.isFresh;
  const backendConnected = !backendDown && (Boolean(pub.data) || Boolean(freshStatus));

  const realX402 =
    paymentMode(state?.handshake?.mandate) === "x402_real" || pub.data?.payment_mode === "x402_real";

  // Demo Mode only: soft notice when backend sync is unavailable (Live Mode uses
  // the blocking LiveUnavailableCard instead, so this never shows in Live Mode).
  const showNotice =
    isLive && !IS_LIVE_MODE && (syncWarn || Boolean(pub.error)) && !noticeDismissed;

  // Single source of truth for the status pill, driven by readiness + phase:
  //   0/5–4/5      → "Waiting for friends"
  //   5/5, no plan → "Agents working"
  //   plan + proof → "Booked · proof ready"
  const phase = useMemo<{ label: string; kind: PillKind }>(() => {
    if (stage === "final") return { label: "Booked · proof ready", kind: "paid" };
    if (stage === "working" || allSubmitted) return { label: "Agents working", kind: "active" };
    return { label: "Waiting for friends", kind: "warning" };
  }, [stage, allSubmitted]);

  const demoFriends: DemoFriend[] = roomMembers.map((m) => ({
    id: m.id,
    name: m.name,
    ready: isMemberReady(m.id),
    onSimulate: () => void submitMember(m, personaById(m.id)?.demo),
  }));

  const demoSections: DemoSection[] = [
    {
      title: "Jump to",
      actions: [
        { label: "Ready all 5", hint: "Submit everyone now", onClick: () => void readyAll(), tone: "primary" },
        { label: "Show waiting room", hint: "Submit 3 of 5", onClick: () => void submitThree() },
        { label: "Agent workbench", hint: "Skip to agents working", onClick: jumpToWorking },
        { label: "Reveal x402 proof", hint: "Jump to the receipt", onClick: revealProof },
      ],
    },
    {
      title: "Room",
      actions: [
        { label: "Reset room", hint: "Fresh room + new links", onClick: () => void resetDemo() },
      ],
    },
  ];

  // Live Mode blocking state: the backend couldn't create/reach the room. Show a
  // real error with Retry — never a fake sample room. (Demo Mode never blocks.)
  if (IS_LIVE_MODE && isLive && backendDown) {
    return (
      <div className={s.room}>
        <LiveUnavailableCard
          onRetry={() => void retryLive()}
          retrying={retrying}
          failedEndpoint="/api/session/live"
          statusText="unreachable"
        />
      </div>
    );
  }

  return (
    <div className={s.room}>
      <GroupRoomHeader
        group={group}
        sessionId={sessionId}
        members={roomMembers}
        readyIds={readyIds}
        phaseLabel={phase.label}
        phaseKind={phase.kind}
        onBack={onBack}
      />

      {isLive ? <SafetyNote realX402={realX402} /> : null}

      {showNotice ? (
        <div className={s.notice} role="status">
          <span className={s.noticeDot} aria-hidden />
          <div className={s.noticeText}>
            <strong>Live sync is warming up.</strong>
            <span>You can continue with the sample room.</span>
            <span className={s.noticeTech}>Backend sync unavailable.</span>
          </div>
          <button type="button" className={s.noticeBtn} onClick={() => setNoticeDismissed(true)}>
            Continue demo
          </button>
        </div>
      ) : null}

      {isLive && stage === "social" ? (
        <InvitePanel
          roomId={sessionId}
          members={roomMembers}
          readyIds={readyIds}
          localRoom={localRoom}
          liveMode={IS_LIVE_MODE}
        />
      ) : null}

      <div className={s.roomGrid}>
        <section className={s.colConstraints} aria-label="Member constraints">
          <div className={s.colHead}>
            <h2 className={s.colTitle}>The squad</h2>
            {isLive && !allSubmitted ? (
              <button
                type="button"
                className={s.btnPrimarySm}
                onClick={() => void readyAll()}
                disabled={busy}
              >
                {busy ? "Submitting…" : "Ready all 5"}
              </button>
            ) : null}
          </div>

          <div className={s.memberList}>
            {roomMembers.length === 0 ? (
              <p className={s.muted}>Assembling the group…</p>
            ) : (
              roomMembers.map((m) => {
                const ready = isMemberReady(m.id);
                const value = submitted[m.id]
                  ? (forms[m.id] ?? emptyConstraints())
                  : (constraintsFromPublic(pubById[m.id]) ?? forms[m.id] ?? emptyConstraints());
                return (
                  <MemberConstraintCard
                    key={m.id}
                    member={m}
                    value={value}
                    onChange={(next) => setForms((p) => ({ ...p, [m.id]: next }))}
                    onSubmit={() => void submitMember(m)}
                    onEdit={() => editMember(m.id)}
                    submitted={ready}
                    submitting={Boolean(submitting[m.id])}
                    editable={isLive && Boolean(submitted[m.id])}
                  />
                );
              })
            )}
          </div>
        </section>

        <section className={s.colStage} aria-label="Live status" ref={stageRef}>
          {stage === "social" ? (
            <WaitingRoom
              members={roomMembers}
              readyIds={readyIds}
              nudged={nudged}
              onNudge={nudge}
              activity={activity}
            />
          ) : null}

          {stage === "working" ? <AgentTimeline activeStep={timelineStep} state={state} /> : null}

          {stage === "final" ? (
            <div className={s.finalStack}>
              {failed ? (
                <LiveFailureCard
                  failReason={failReason}
                  venueStatus={venueStatus}
                  onEditConstraints={backToRoom}
                  onRetry={retryPlan}
                  onRelax={backToRoom}
                  onBackToWaiting={backToRoom}
                  showTechnical={PRESENTER_TOOLS_VISIBLE}
                />
              ) : noVenue || !displayPlan ? (
                <NoVerifiedVenueCard />
              ) : (
                <>
                  <FinalPlanCard plan={displayPlan} members={roomMembers} />
                  <TrustPanel
                    verification={displayPlan.venue?.verification}
                    split={split}
                    venueName={displayPlan.venue?.name}
                    receipt={activeReceipt}
                    awaitingFreshProof={awaitingFreshProof}
                  />
                  <MerchantAdapterCard verification={displayPlan.venue?.verification} />
                  <PaymentProofCard receipt={activeReceipt} awaitingFreshProof={awaitingFreshProof} />
                  <p className={s.muted}>
                    0.001 USDC is a testnet proof-of-mechanism. In production, the full meal amount
                    settles on-chain.
                  </p>
                  <SmartSplitCard
                    split={split}
                    mode={splitMode}
                    onModeChange={setSplitMode}
                    source={splitSource}
                    showFallbackNote={splitFellBack}
                  />
                  {/* Share the group-mandate receipt card (native share / WhatsApp /
                      copy-link live inside that page). Same-origin via the API rewrite. */}
                  {sessionId ? (
                    <a
                      className={s.btnGhost}
                      href={`${API_BASE}/receipt/${sessionId}/card`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Share receipt ↗
                    </a>
                  ) : null}
                  {/* Secondary, bottom-of-screen CTA — reuse this crew next time.
                      Never for ORK-001 (the frozen seeded demo). */}
                  {isLive && sessionId && sessionId !== "ORK-001" ? (
                    <>
                      <button
                        type="button"
                        className={s.btnGhost}
                        onClick={() => void planAnother()}
                        disabled={cloning}
                      >
                        {cloning
                          ? "Setting up your next dinner…"
                          : "Friday coming up? Plan another dinner with this crew →"}
                      </button>
                      {cloneError ? <p className={s.muted}>{cloneError}</p> : null}
                    </>
                  ) : null}
                </>
              )}
            </div>
          ) : null}
        </section>
      </div>

      {isLive && PRESENTER_TOOLS_VISIBLE ? <HowToDemo /> : null}

      {isLive && PRESENTER_TOOLS_VISIBLE ? <WalletLab receipt={activeReceipt} /> : null}

      {isLive && PRESENTER_TOOLS_VISIBLE ? (
        <LiveReadinessPanel
          backendConnected={backendConnected}
          verification={planVerification}
          freshStatus={freshStatus}
          splitSource={splitSource}
          bookingMode={bookingMode}
        />
      ) : null}

      {isLive && PRESENTER_TOOLS_VISIBLE ? (
        <DemoControls
          friends={demoFriends}
          autoRun={{
            label: "Auto-run friend replies",
            hint: "One friend submits every ~1–2s",
            onClick: () => void autoRun(),
          }}
          sections={demoSections}
          busy={busy}
          autoRunning={autoRunning}
          title={MODE_COPY.presenterToolsTitle}
          subtitle={MODE_COPY.presenterToolsSub}
          toggleLabel={MODE_COPY.presenterToolsToggle}
          simulationOnly={IS_LIVE_MODE}
        />
      ) : null}
    </div>
  );
}
