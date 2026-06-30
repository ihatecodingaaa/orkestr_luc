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
import type { DemoAction } from "./DemoControls";
import type { ActivityItem } from "./ActivityFeed";
import type { PillKind } from "./primitives";
import {
  DEMO_PLAN,
  PERSONAS,
  SEED_CHAT,
  TIMELINE_STEPS,
  emptyConstraints,
  isReadyToSubmit,
  personaById,
  personName,
} from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import {
  createLiveSession,
  getStatus,
  markReady,
  readLiveState,
  submitConstraints,
} from "@/lib/orkestr-api";
import type { Constraints, LiveState, Member } from "@/lib/orkestr-api";
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

export function GroupRoom({ group, onBack }: { group: DemoGroup; onBack: () => void }) {
  const isLive = group.mode === "live";
  const initialMembers = isLive ? [] : PERSONA_MEMBERS;

  const [sessionId, setSessionId] = useState<string | null>(null);
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
  const [error, setError] = useState<string | null>(null);

  const [nudged, setNudged] = useState<string[]>([]);
  const [activity, setActivity] = useState<ActivityItem[]>(seedActivity);
  const [busy, setBusy] = useState(false);

  // Refs let us trigger the pipeline from the submit handler (the event that
  // makes it 5/5) instead of a render effect — avoids cascading-render lint and
  // matches "you might not need an effect".
  const submittedRef = useRef<Set<string>>(new Set());
  const startedRef = useRef(false);

  const pushActivity = useCallback((item: Omit<ActivityItem, "id">) => {
    setActivity((prev) => [
      ...prev,
      { ...item, id: `${item.kind}-${Date.now()}-${Math.round(Math.random() * 1e6)}` },
    ]);
  }, []);

  // Default demo-safe path: animate the workbench and reveal the already
  // verified public receipt. A live backend pipeline is opt-in only because it
  // can run booking/payment work depending on server configuration.
  const runPipeline = useCallback(
    async (sid: string) => {
      if (startedRef.current) return;
      startedRef.current = true;
      setWorking(true);
      setError(null);
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
        setError(e instanceof Error ? e.message : "The agents hit a snag");
        startedRef.current = false;
      } finally {
        setWorking(false);
      }
    },
    [pushActivity],
  );

  // Spin up a real live session (5 member identities) for the interactive group.
  useEffect(() => {
    if (!isLive) return;
    let active = true;
    (async () => {
      try {
        const live = await createLiveSession();
        if (!active) return;
        const ms = live.members?.length ? live.members : PERSONA_MEMBERS;
        setSessionId(live.session_id);
        setMembers(ms);
        setForms(memberForms(ms));
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Unable to reach the backend");
      }
    })();
    return () => {
      active = false;
    };
  }, [isLive]);

  const readyIds = useMemo(
    () => members.filter((m) => submitted[m.id]).map((m) => m.id),
    [members, submitted],
  );
  const allSubmitted = members.length > 0 && members.every((m) => submitted[m.id]);

  const submitMember = useCallback(
    async (member: Member, override?: Constraints) => {
      if (!sessionId) return;
      const c = override ?? forms[member.id];
      if (!c || !isReadyToSubmit(c)) return;
      setSubmitting((p) => ({ ...p, [member.id]: true }));
      if (override) setForms((p) => ({ ...p, [member.id]: override }));
      try {
        await submitConstraints(sessionId, member, c);
        await markReady(sessionId, member.id);
        setSubmitted((p) => ({ ...p, [member.id]: true }));
        submittedRef.current.add(member.id);
        pushActivity({ kind: "system", text: `${member.name} submitted their constraints` });
        // When this submit makes it 5/5, kick off the pipeline from the event.
        if (members.length > 0 && submittedRef.current.size >= members.length) {
          void runPipeline(sessionId);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unable to submit constraints");
      } finally {
        setSubmitting((p) => ({ ...p, [member.id]: false }));
      }
    },
    [sessionId, forms, members.length, pushActivity, runPipeline],
  );

  const editMember = useCallback((id: string) => {
    // Local-only edit — there is no backend unready; re-submitting overwrites it.
    setSubmitted((p) => ({ ...p, [id]: false }));
    submittedRef.current.delete(id);
  }, []);

  const readyAll = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await Promise.all(members.map((m) => submitMember(m, personaById(m.id)?.demo)));
    } finally {
      setBusy(false);
    }
  }, [sessionId, members, submitMember]);

  const submitThree = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await Promise.all(members.slice(0, 3).map((m) => submitMember(m, personaById(m.id)?.demo)));
    } finally {
      setBusy(false);
    }
  }, [sessionId, members, submitMember]);

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
    setError(null);
    submittedRef.current = new Set();
    startedRef.current = false;
    try {
      const live = await createLiveSession();
      const ms = live.members?.length ? live.members : PERSONA_MEMBERS;
      setSessionId(live.session_id);
      setMembers(ms);
      setForms(memberForms(ms));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to reach the backend");
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

  const finalPlan = state?.plan ?? DEMO_PLAN;
  const roomMembers = members.length ? members : PERSONA_MEMBERS;
  const perPerson = typeof finalPlan.per_person === "number" ? finalPlan.per_person : 80;

  const phase = useMemo<{ label: string; kind: PillKind }>(() => {
    if (stage === "final") return { label: "Booked & paid", kind: "paid" };
    if (stage === "working") return { label: "Agents working", kind: "active" };
    if (readyIds.length > 0) return { label: "Waiting for friends", kind: "warning" };
    return { label: "Collecting constraints", kind: "info" };
  }, [stage, readyIds.length]);

  const demoActions: DemoAction[] = [
    {
      label: "Ready all 5 friends",
      hint: "Submits demo constraints and opens the workbench",
      onClick: () => void readyAll(),
      tone: "primary",
    },
    {
      label: "Show the waiting room",
      hint: "Submit 3 of 5 to see the social wait",
      onClick: () => void submitThree(),
    },
    {
      label: "Reveal x402 proof",
      hint: "Jump to the verified payment receipt",
      onClick: () => setRevealed(true),
    },
    {
      label: "Reset demo",
      hint: "Start a fresh dinner room",
      onClick: () => void resetDemo(),
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

      {error ? (
        <div className={s.errorBar} role="status">
          <span>{error}.</span>
          <button type="button" className={s.linkBtn} onClick={() => setRevealed(true)}>
            Show the sample result
          </button>
        </div>
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
                disabled={busy || !sessionId}
              >
                {busy ? "Submitting…" : "Ready all 5"}
              </button>
            ) : null}
          </div>

          <div className={s.memberList}>
            {members.length === 0 ? (
              <p className={s.muted}>Assembling the group…</p>
            ) : (
              members.map((m) => (
                <MemberConstraintCard
                  key={m.id}
                  member={m}
                  value={forms[m.id] ?? emptyConstraints()}
                  onChange={(next) => setForms((p) => ({ ...p, [m.id]: next }))}
                  onSubmit={() => void submitMember(m)}
                  onEdit={() => editMember(m.id)}
                  submitted={Boolean(submitted[m.id])}
                  submitting={Boolean(submitting[m.id])}
                  editable={isLive}
                />
              ))
            )}
          </div>
        </section>

        <section className={s.colStage} aria-label="Live status">
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

      {isLive ? <DemoControls actions={demoActions} busy={busy} /> : null}
    </div>
  );
}
