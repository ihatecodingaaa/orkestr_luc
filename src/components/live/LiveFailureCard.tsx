"use client";

import s from "./live.module.css";

// Turn a backend fail_reason / venue_status into calm, user-friendly copy. We
// never dump a raw backend string on a normal user — the presenter can still see
// the exact reason via the technical line below.
function friendlyReason(
  failReason?: string | null,
  venueStatus?: string | null,
): { subtitle: string; hint: string } {
  const r = (failReason ?? "").toLowerCase();
  // Specific booking/payment failures first (their reasons also mention "venue"
  // or "booking", so they must win over the generic no-verified-venue branch).
  if (r.includes("timed out")) {
    return {
      subtitle: "The venue's booking service didn't respond — nothing was booked.",
      hint: "Try again in a moment, or reserve directly via the venue links.",
    };
  }
  if (r.includes("payment")) {
    return {
      subtitle: "The payment couldn't be authorized — nothing was booked and no charge was made.",
      hint: "Try again, or adjust the plan and re-run it.",
    };
  }
  if (r.includes("booking service") || r.includes("reservation request")) {
    return {
      subtitle: "The venue's booking service returned an error — nothing was booked.",
      hint: "Try again, or reserve directly via Google Maps or by calling the venue.",
    };
  }
  if (
    venueStatus === "no_verified_venue" ||
    r.includes("verified") ||
    r.includes("venue") ||
    r.includes("restaurant")
  ) {
    return {
      subtitle: "No verified restaurant matched the group's constraints.",
      hint: "Try relaxing budget, location, or dietary needs, then search again.",
    };
  }
  if (r.includes("agree") || r.includes("quorum") || r.includes("could")) {
    return {
      subtitle: "The agents couldn't reach agreement across everyone's constraints.",
      hint: "Loosen a constraint or two, then try again.",
    };
  }
  if (r.includes("integrity") || r.includes("mandate") || r.includes("tamper")) {
    return {
      subtitle: "The booking integrity check failed — nothing was booked and no payment was made.",
      hint: "This is a safety stop. You can try planning again.",
    };
  }
  return {
    subtitle: "We couldn't finish planning this room.",
    hint: "Adjust the group's constraints and try again.",
  };
}

// Polished failure state for a live room that could not complete (quorum, no
// verified venue, or a mandate integrity block). In live mode this REPLACES the
// plan entirely — never a sample venue, never a stale payment proof.
export function LiveFailureCard({
  failReason,
  venueStatus,
  suggestedMinBudget,
  onEditConstraints,
  onRetry,
  onRelax,
  onBackToWaiting,
  showTechnical = false,
}: {
  failReason?: string | null;
  venueStatus?: string | null;
  suggestedMinBudget?: number | null;
  onEditConstraints?: () => void;
  onRetry?: () => void;
  onRelax?: () => void;
  onBackToWaiting?: () => void;
  showTechnical?: boolean;
}) {
  const { subtitle, hint } = friendlyReason(failReason, venueStatus);
  const hasActions = onEditConstraints || onRetry || onRelax || onBackToWaiting;
  const showNudge = typeof suggestedMinBudget === "number" && suggestedMinBudget > 0;
  return (
    <div className={`${s.noVenueCard} ${s.failCard}`} role="alert">
      <div className={s.noVenueIcon} aria-hidden>
        ⚠️
      </div>
      <h2 className={s.noVenueTitle}>We couldn&apos;t complete this plan.</h2>
      <p className={s.noVenueSub}>{subtitle}</p>
      <p className={s.failHint}>{hint}</p>
      {showNudge ? (
        <p className={s.failHint}>
          Confirmed-price restaurants for this group start around ~${suggestedMinBudget}/head — try
          raising the minimum budget.
        </p>
      ) : null}

      {hasActions ? (
        <div className={s.failActions}>
          {onEditConstraints ? (
            <button type="button" className={s.btnPrimarySm} onClick={onEditConstraints}>
              Edit constraints
            </button>
          ) : null}
          {onRetry ? (
            <button type="button" className={s.btnGhost} onClick={onRetry}>
              Try again
            </button>
          ) : null}
          {onRelax ? (
            <button type="button" className={s.btnGhost} onClick={onRelax}>
              Relax constraints
            </button>
          ) : null}
          {onBackToWaiting ? (
            <button type="button" className={s.btnGhost} onClick={onBackToWaiting}>
              Back to waiting room
            </button>
          ) : null}
        </div>
      ) : null}

      {showTechnical && failReason ? (
        <p className={s.failTech}>
          <span aria-hidden>🛈 </span>
          Technical detail: {failReason}
        </p>
      ) : null}
    </div>
  );
}
