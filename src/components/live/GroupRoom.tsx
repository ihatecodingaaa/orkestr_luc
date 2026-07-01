"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GroupRoomHeader } from "./GroupRoomHeader";
import { MemberConstraintCard } from "./MemberConstraintCard";
import { WaitingRoom } from "./WaitingRoom";
import { AgentTimeline } from "./AgentTimeline";
import { FinalPlanCard } from "./FinalPlanCard";
import { PaymentProofCard } from "./PaymentProofCard";
import { SettlementCard } from "./SettlementCard";
import { DemoControls } from "./DemoControls";
import type { DemoFriend, DemoSection } from "./DemoControls";
import { InvitePanel } from "./InvitePanel";
import type { ActivityItem } from "./ActivityFeed";
import type { PillKind } from "./primitives";
import {
  DEMO_PLAN,
  PERSONAS,
  SEED_CHAT,
  TIMELINE_STEPS,
  X402_PROOF,
  emptyConstraints,
  isReadyToSubmit,
  localRoomId,
  personaById,
  personName,
  presenterLink,
} from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import {
  createLiveSession,
  getStatus,
  markReady,
  paymentMode,
  readLiveState,
  shortHash,
  submitConstraints,
} from "@/lib/orkestr-api";
import type { Constraints, LiveState, Member, PublicMember } from "@/lib/orkestr-api";
import { usePublicSession } from "@/lib/useRoom";
import s from "./live.module.css";

const PERSONA_MEMBERS: Member[] = PERSONAS.map((p) => ({ id: p.id, name: p.name }));
const ENABLE_LIVE_PIPELINE = process.env.NEXT_PUBLIC_ENABLE_LIVE_PIPELINE === "true";

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
        <strong>Demo-safe payment mode</strong>
        <span>No live USDC is spent here — the verified x402 receipt is shown as proof.</span>
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
function WalletLab() {
  const [open, setOpen] = useState(false);
  const p = X402_PROOF;
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
  useEffect(() => {
    if (!isLive) return;
    let active = true;
    (async () => {
      if (boundRoomId) {
        setSessionId(boundRoomId);
        setMembers(PERSONA_MEMBERS);
        setForms(memberForms(PERSONA_MEMBERS));
        return;
      }
      try {
        const live = await createLiveSession();
        if (!active) return;
        const ms = live.members?.length ? live.members : PERSONA_MEMBERS;
        setSessionId(live.session_id);
        setMembers(ms);
        setForms(memberForms(ms));
        if (typeof window !== "undefined") {
          window.history.replaceState(null, "", presenterLink(live.session_id));
        }
      } catch (e) {
        if (!active) return;
        // Backend unreachable — fall back to a fully local demo room so the
        // presenter can still run Demo Cast, share invite links, and reach the
        // reveal. The real error stays in the console; the UI stays calm.
        console.warn("[orkestr] live room unavailable — running a local demo room", e);
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
    })();
    return () => {
      active = false;
    };
  }, [isLive, boundRoomId]);

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
    if (!isLive || !sessionId || startedRef.current) return;
    if (roomMembers.length === 0 || readyIds.length < roomMembers.length) return;
    const t = window.setTimeout(() => void runPipeline(sessionId), 0);
    return () => window.clearTimeout(t);
  }, [isLive, sessionId, roomMembers.length, readyIds.length, runPipeline]);

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
      if (typeof window !== "undefined") {
        window.history.replaceState(null, "", presenterLink(live.session_id));
      }
    } catch (e) {
      // Stay usable on a local demo room if the backend is unreachable.
      console.warn("[orkestr] reset could not reach the backend — using a local demo room", e);
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

  const finalPlan = state?.plan ?? DEMO_PLAN;
  const perPerson = typeof finalPlan.per_person === "number" ? finalPlan.per_person : 80;

  const realX402 =
    paymentMode(state?.handshake?.mandate) === "x402_real" || pub.data?.payment_mode === "x402_real";

  // Presenter-only, demo-safe notice when backend sync is unavailable (a failed
  // write or the polling endpoint being unreachable). Never shows raw error text.
  const showNotice = isLive && (syncWarn || Boolean(pub.error)) && !noticeDismissed;

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
              <FinalPlanCard plan={finalPlan} members={roomMembers} />
              <PaymentProofCard />
              <SettlementCard perPerson={perPerson} members={roomMembers} />
            </div>
          ) : null}
        </section>
      </div>

      {isLive ? <HowToDemo /> : null}

      {isLive ? <WalletLab /> : null}

      {isLive ? (
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
        />
      ) : null}
    </div>
  );
}
