"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Avatar, StatusPill, Wordmark } from "./primitives";
import { AgentTimeline } from "./AgentTimeline";
import { FinalPlanCard } from "./FinalPlanCard";
import { PaymentProofCard } from "./PaymentProofCard";
import { ParticipantSplitCard } from "./ParticipantSplitCard";
import {
  COPY,
  DAY_OPTIONS,
  DEMO_PLAN,
  DIET_OPTIONS,
  PERSONAS,
  TIMELINE_STEPS,
  cloneDemoConstraints,
  formatNames,
  isReadyToSubmit,
  personaById,
} from "@/lib/demo";
import { markReady, submitConstraints } from "@/lib/orkestr-api";
import type { Constraints, Member } from "@/lib/orkestr-api";
import { usePublicSession, useSplit } from "@/lib/useRoom";
import s from "./live.module.css";

const ENABLE_LIVE_PIPELINE = process.env.NEXT_PUBLIC_ENABLE_LIVE_PIPELINE === "true";

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

const PERSONA_MEMBERS: Member[] = PERSONAS.map((p) => ({ id: p.id, name: p.name }));

type Stage = "form" | "waiting" | "working" | "final";

export function ParticipantView({ roomId, memberId }: { roomId: string; memberId: string }) {
  const persona = personaById(memberId);
  const myName = persona?.name ?? memberId;
  const me: Member = useMemo(() => ({ id: memberId, name: myName }), [memberId, myName]);
  // Your share uses the backend split when the live pipeline is on and this room
  // is a real backend session; otherwise the local calculator. No scary warnings
  // on a participant's phone — just the numbers and a subtle source badge.
  const { split: mySplit, source: splitSource } = useSplit(roomId, "itemized", {
    enabled: ENABLE_LIVE_PIPELINE && Boolean(roomId),
  });

  // Prefill with this friend's sensible defaults so "Send to my agent" is a
  // single tap during a fast demo — still fully editable.
  const [form, setForm] = useState<Constraints>(() =>
    persona ? cloneDemoConstraints(persona.demo) : { days: [], budget: "", dietary: [], freeform: "" },
  );
  const [localSubmitted, setLocalSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [working, setWorking] = useState(false);
  const [timelineStep, setTimelineStep] = useState(0);

  const { data } = usePublicSession(roomId, { intervalMs: 2200 });
  const startedRef = useRef(false);

  const roomName = data?.name ?? "Friday Dinner Crew";
  const total = data?.members_total || 5;

  // Backend may show me as ready if I submitted from this phone *or* the
  // presenter simulated me (hybrid demo) — either way I'm in.
  const backendReadyMe = Boolean(data?.ready?.includes(memberId));
  const iAmReady = localSubmitted || backendReadyMe;

  const readySet = useMemo(() => {
    const set = new Set<string>(data?.ready ?? []);
    if (localSubmitted) set.add(memberId);
    return set;
  }, [data?.ready, localSubmitted, memberId]);
  const readyCount = Math.min(readySet.size, total);
  const allReady = readyCount >= total && total > 0;

  const roster: Member[] = data?.members?.length
    ? data.members.map((m) => ({ id: m.id, name: m.name }))
    : PERSONA_MEMBERS;
  const pending = roster.filter((m) => !readySet.has(m.id));

  const submit = useCallback(async () => {
    if (!isReadyToSubmit(form) || submitting) return;
    setSubmitting(true);
    try {
      await submitConstraints(roomId, me, form);
      await markReady(roomId, memberId);
    } catch (e) {
      // Backend unreachable — keep going locally so the phone demo never stalls.
      // The real error still reaches the console; the UI stays calm.
      console.warn("[orkestr] could not reach the room — continuing locally", e);
    } finally {
      setLocalSubmitted(true);
      setSubmitting(false);
    }
  }, [form, submitting, roomId, me, memberId]);

  // Once everyone is in, run the same reveal animation locally (demo-safe: the
  // verified receipt is static, identical on every device — no pipeline call).
  useEffect(() => {
    if (!iAmReady || !allReady || startedRef.current) return;
    startedRef.current = true;
    setWorking(true);
    setTimelineStep(1);
    const reveal = window.setTimeout(() => {
      setWorking(false);
      setTimelineStep(TIMELINE_STEPS.length);
      setRevealed(true);
    }, 5200);
    return () => window.clearTimeout(reveal);
  }, [iAmReady, allReady]);

  useEffect(() => {
    if (!working) return;
    const id = window.setInterval(() => {
      setTimelineStep((prev) => (prev < TIMELINE_STEPS.length - 1 ? prev + 1 : prev));
    }, 1100);
    return () => window.clearInterval(id);
  }, [working]);

  const stage: Stage = revealed ? "final" : working ? "working" : iAmReady ? "waiting" : "form";

  const dietLabels = form.dietary.map((id) => DIET_OPTIONS.find((d) => d.id === id)?.label ?? id);

  return (
    <div className={s.phone}>
      <header className={s.phoneTop}>
        <Wordmark small />
        <StatusPill kind={stage === "final" ? "paid" : iAmReady ? "ready" : "live"}>
          {stage === "final" ? "Booked & paid" : iAmReady ? "You're in" : "Live room"}
        </StatusPill>
      </header>

      <div className={s.phoneHero}>
        <Avatar id={memberId} size={52} ring />
        <div className={s.phoneHeroText}>
          <span className={s.phoneRoom}>{roomName}</span>
          <h1 className={s.phoneYou}>You are {myName}</h1>
          {persona ? <p className={s.phoneVibe}>{persona.vibe}</p> : null}
        </div>
      </div>

      <ol className={s.phoneSteps} aria-label="Your progress">
        {["Your picks", "Wait with friends", "Agents plan", "Booked & paid"].map((label, i) => {
          const idx = stage === "form" ? 0 : stage === "waiting" ? 1 : stage === "working" ? 2 : 3;
          const cls = i < idx ? s.stepDone : i === idx ? s.stepNow : "";
          return (
            <li key={label} className={`${s.phoneStep} ${cls}`}>
              <span className={s.phoneStepDot}>{i < idx ? "✓" : i + 1}</span>
              <span className={s.phoneStepLabel}>{label}</span>
            </li>
          );
        })}
      </ol>

      {stage === "form" ? (
        <section className={s.phoneCard} aria-label="Submit your constraints">
          <h2 className={s.phoneCardTitle}>What works for you?</h2>
          <p className={s.phoneCardSub}>
            Your agent takes it from here — no group chat back-and-forth.
          </p>

          <div className={s.field}>
            <span className={s.fieldLabel}>Days that work</span>
            <div className={s.chipRow}>
              {DAY_OPTIONS.map((day) => (
                <button
                  key={day}
                  type="button"
                  className={`${s.chip} ${form.days.includes(day) ? s.chipOn : ""}`}
                  onClick={() => setForm({ ...form, days: toggle(form.days, day) })}
                >
                  {day}
                </button>
              ))}
            </div>
          </div>

          <div className={s.field}>
            <span className={s.fieldLabel}>Budget / head</span>
            <div className={s.budgetWrap}>
              <span className={s.budgetSign}>$</span>
              <input
                className={s.budgetInput}
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="60"
                value={form.budget}
                onChange={(e) => setForm({ ...form, budget: e.target.value })}
              />
            </div>
          </div>

          <div className={s.field}>
            <span className={s.fieldLabel}>Dietary needs</span>
            <div className={s.chipRow}>
              {DIET_OPTIONS.map((diet) => (
                <button
                  key={diet.id}
                  type="button"
                  className={`${s.chip} ${form.dietary.includes(diet.id) ? s.chipOn : ""}`}
                  onClick={() => setForm({ ...form, dietary: toggle(form.dietary, diet.id) })}
                >
                  {diet.label}
                </button>
              ))}
            </div>
          </div>

          <div className={s.field}>
            <span className={s.fieldLabel}>Vibe / anything else</span>
            <input
              className={s.textInput}
              type="text"
              placeholder="e.g. walkable, group seating"
              value={form.freeform}
              onChange={(e) => setForm({ ...form, freeform: e.target.value })}
            />
          </div>

          <button
            type="button"
            className={s.phoneSubmit}
            onClick={() => void submit()}
            disabled={!isReadyToSubmit(form) || submitting}
          >
            {submitting ? "Sending…" : "Send to my agent →"}
          </button>
          {!isReadyToSubmit(form) ? (
            <span className={s.phoneHint}>Pick at least one day and a budget to continue.</span>
          ) : null}
        </section>
      ) : null}

      {stage === "waiting" ? (
        <section className={s.phoneCard} aria-label="Waiting for friends">
          <div className={s.phoneConfirm}>
            <span className={s.phoneConfirmMark} aria-hidden>
              ✓
            </span>
            <div>
              <strong>{myName}&apos;s agent is ready</strong>
              <span>Your picks are locked in and negotiating on your behalf.</span>
            </div>
          </div>

          <div className={s.phoneSummary}>
            {form.days.map((d) => (
              <span key={d} className={s.miniChip}>
                {d}
              </span>
            ))}
            {form.budget ? <span className={s.miniChip}>${form.budget}/head</span> : null}
            {dietLabels.map((d) => (
              <span key={d} className={`${s.miniChip} ${s.miniChipDiet}`}>
                {d}
              </span>
            ))}
          </div>

          <div className={s.phoneProgress}>
            <div className={s.phoneProgressBar}>
              <span
                className={s.phoneProgressFill}
                style={{ width: `${(readyCount / total) * 100}%` }}
              />
            </div>
            <strong className={s.phoneProgressText}>
              {readyCount} of {total} friends replied
            </strong>
            <span className={s.phoneProgressSub}>
              {pending.length === 0
                ? "Everyone is in — agents are starting"
                : pending.length > 2
                  ? `Waiting for ${pending.length} more friends`
                  : `Waiting for ${formatNames(pending.map((p) => p.name))}`}
            </span>
          </div>

          <div className={s.phoneRoster}>
            {roster.map((m) => {
              const ready = readySet.has(m.id);
              return (
                <div key={m.id} className={`${s.phoneRosterRow} ${ready ? s.phoneRosterReady : ""}`}>
                  <Avatar id={m.id} size={30} ring={ready} />
                  <span className={s.phoneRosterName}>{m.name}</span>
                  <StatusPill kind={ready ? "ready" : "pending"}>
                    {ready ? "Ready" : "Pending"}
                  </StatusPill>
                </div>
              );
            })}
          </div>

          <p className={s.phoneWaitNote}>
            {allReady ? COPY.agentsWorkingHint : COPY.participantWaitHint}
          </p>
        </section>
      ) : null}

      {stage === "working" ? (
        <section className={s.phoneStage} aria-label="Agents finding the plan">
          <AgentTimeline activeStep={timelineStep} state={null} />
        </section>
      ) : null}

      {stage === "final" ? (
        <section className={s.phoneStage} aria-label="Your plan">
          <FinalPlanCard plan={DEMO_PLAN} members={roster} />
          <PaymentProofCard />
          <ParticipantSplitCard
            split={mySplit}
            memberId={memberId}
            myName={myName}
            source={splitSource}
          />
        </section>
      ) : null}

      <footer className={s.phoneFoot}>
        <Wordmark small />
        <span>Mobile-first group dining · no app install needed</span>
      </footer>
    </div>
  );
}
