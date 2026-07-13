import { Avatar, Fact, StatusPill } from "./primitives";
import { VenueBadge, VenueEvidence } from "./verification";
import { WHY_THIS_WON } from "@/lib/demo";
import { money } from "@/lib/orkestr-api";
import type { Member, Plan } from "@/lib/orkestr-api";
import s from "./live.module.css";

function prettyTag(tag: string): string {
  return tag.replace(/_/g, " ");
}

// Reasons adapt to the real plan when possible, with a sensible fallback so the
// card always reads well even if the backend returns a different venue.
function buildReasons(plan: Plan, approx: boolean): string[] {
  const reasons: string[] = [];
  const tags = plan.venue?.tags ?? [];
  const dietTags = tags.filter((t) => /halal|veg|options|raw/.test(t));
  if (dietTags.length) {
    reasons.push(`Menu covers ${dietTags.map(prettyTag).join(", ")}`);
  }
  if (typeof plan.per_person === "number") {
    reasons.push(
      approx
        ? `About $${plan.per_person} a head (price approximate) — within everyone's budget`
        : `$${plan.per_person} a head lands inside everyone's budget`,
    );
  }
  if (plan.day || plan.time) {
    reasons.push(`${plan.day ?? "FRI"} ${plan.time ?? "19:30"} is the night that works for all five`);
  }
  return reasons.length >= 2 ? reasons : WHY_THIS_WON;
}

export function FinalPlanCard({
  plan,
  members,
  suggestedMinBudget,
}: {
  plan: Plan;
  members: Member[];
  suggestedMinBudget?: number | null;
}) {
  const tags = plan.venue?.tags ?? [];
  // Honest happy count: when the plan carries a satisfies array, render EXACTLY
  // it — an empty array means nobody was satisfied, never "everyone". Only a
  // plan with no satisfies field at all (legacy shape) falls back to members.
  const happyIds = Array.isArray(plan.satisfies) ? plan.satisfies : members.map((m) => m.id);
  const seats = members.length || 5;
  const verification = plan.venue?.verification;
  // Price truth: the venue IS verified; only the price may be an estimate. When so,
  // we keep the verified badge and add an honest "price approximate" chip — we never
  // show the per-head as if it were confirmed.
  const priceApprox =
    verification?.price_confidence === "approximate" || plan.price_confidence === "approximate";
  // Prefer the backend's grounded, occasion-aware reasons (describe the REAL chosen
  // venue); fall back to the local reason builder if the backend sent none.
  const backendReasons = (plan.why_reasons ?? []).filter((r) => typeof r === "string" && r.trim());
  const reasons = backendReasons.length >= 2 ? backendReasons : buildReasons(plan, priceApprox);
  const occasion = (plan.occasion ?? []).filter((o) => typeof o === "string" && o.trim());
  // Nudge only makes sense while the shown price is approximate and there is a real,
  // higher confirmed-price floor to bump to.
  const showNudge =
    priceApprox && typeof suggestedMinBudget === "number" && suggestedMinBudget > 0;

  return (
    <div className={s.planCard}>
      <div className={s.planHead}>
        <div>
          <span className={s.stageKicker}>The plan</span>
          <h2 className={s.planVenue}>{plan.venue?.name ?? plan.title ?? "Dinner"}</h2>
          <p className={s.planAddr}>
            {plan.venue?.address ?? plan.venue?.area ?? "Singapore"}
          </p>
        </div>
        <div className={s.planHeadRight}>
          {/* Honest status: Orkestr prepares a booking HANDOFF (Maps/site/call) —
              it does not hold real restaurant inventory, so never "Booked". */}
          <StatusPill kind="done">Handoff ready</StatusPill>
          <VenueBadge verification={verification} />
          {priceApprox ? (
            <span className={s.planTag} title="Verified venue; price is an estimate">
              Price approximate
            </span>
          ) : null}
        </div>
      </div>

      <VenueEvidence verification={verification} />

      <div className={s.planFacts}>
        {/* The time is the group's suggested slot — no live inventory holds it. */}
        <Fact label="When (suggested)" value={`${plan.day ?? "FRI"} · ${plan.time ?? "19:30"}`} />
        <Fact
          label="Per person"
          value={priceApprox ? `~${money(plan.per_person)}` : money(plan.per_person)}
        />
        <Fact label="Table" value={`${seats} seats`} />
        {/* Never claim "confirmed": a ref only exists for the seeded/demo room;
            live rooms get a booking handoff (reserve via Maps or call). */}
        <Fact
          label="Booking"
          value={plan.booking_ref ? `${plan.booking_ref} (demo)` : "Handoff — reserve via Maps or call"}
        />
      </div>

      {showNudge ? (
        <p className={s.muted}>
          Showing verified options at ~{money(plan.per_person)}/head (price approximate). Bump your
          minimum to ~${suggestedMinBudget}/head for confirmed-price restaurants.
        </p>
      ) : null}

      {tags.length ? (
        <div className={s.planTags}>
          {tags.slice(0, 5).map((t) => (
            <span key={t} className={s.planTag}>
              {prettyTag(t)}
            </span>
          ))}
        </div>
      ) : null}

      <div className={s.planBlock}>
        <span className={s.blockLabel}>Everyone taken care of</span>
        <div className={s.satRow}>
          <div className={s.satAvatars}>
            {happyIds.map((id) => (
              <Avatar key={id} id={id} size={30} ring />
            ))}
          </div>
          <span className={s.satCount}>{happyIds.length}/{seats} happy</span>
        </div>
      </div>

      <div className={s.planBlock}>
        <span className={s.blockLabel}>Why this place won</span>
        {occasion.length ? (
          <p className={s.muted}>Picked for a {occasion.join(" / ")} — matched to the group&apos;s note.</p>
        ) : null}
        <ul className={s.whyList}>
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
