"use client";

import { TIMELINE_STEPS } from "@/lib/demo";
import type { LiveState } from "@/lib/orkestr-api";
import s from "./live.module.css";

// activeStep: index of the step currently in progress (steps before it are done).
// When the real pipeline returns, the orchestrator sets activeStep past the end
// so every step reads "done" and the detail lines fill in with real data.
export function AgentTimeline({
  activeStep,
  state,
}: {
  activeStep: number;
  state: LiveState | null;
}) {
  const venue = state?.plan.venue?.name;
  const agree = state?.plan.satisfies?.length ?? 0;
  const bookingRef = state?.handshake.mandate?.booking_ref ?? state?.handshake.booking_ref;

  const details: Record<string, string | undefined> = {
    prefs: "5 of 5 friends submitted",
    discovery: venue ? `Shortlisted ${venue}` : "Scanning venues that fit every diet",
    negotiate: agree ? `${agree} of 5 agents agree` : "Trading off day, budget, and diet",
    booking: bookingRef ? `Table locked · ${bookingRef}` : "Confirming the table with the merchant",
    payment: "0.001 USDC settled on Base Sepolia",
  };

  return (
    <div className={s.timeline}>
      <div className={s.stageHead}>
        <span className={s.stageKicker}>Agent workbench</span>
        <h2 className={s.stageTitle}>Orkestr is putting the night together</h2>
        <p className={s.stageSub}>
          {"Everyone's preferences are in. Here's what's happening behind the scenes."}
        </p>
      </div>

      <ol className={s.timelineList}>
        {TIMELINE_STEPS.map((step, i) => {
          const status = i < activeStep ? "done" : i === activeStep ? "active" : "pending";
          const isLast = i === TIMELINE_STEPS.length - 1;
          return (
            <li key={step.key} className={`${s.tlItem} ${s[`tl_${status}`]}`}>
              <div className={s.tlMarker} aria-hidden>
                <span className={s.tlDot}>{status === "done" ? "✓" : i + 1}</span>
                {!isLast ? <span className={s.tlLine} /> : null}
              </div>
              <div className={s.tlBody}>
                <div className={s.tlTop}>
                  <strong>{step.title}</strong>
                  <span className={s.tlTag}>{step.tag}</span>
                </div>
                <p className={s.tlCaption}>{step.caption}</p>
                {status !== "pending" ? (
                  <span className={s.tlDetail}>
                    <span className={s.tlDetailMark} aria-hidden>
                      {status === "done" ? "✓" : "•"}
                    </span>
                    {details[step.key]}
                  </span>
                ) : null}
                {status === "active" ? <span className={s.tlProgress} aria-hidden /> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
