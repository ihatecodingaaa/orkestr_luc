import { Avatar, Fact, StatusPill } from "./primitives";
import { WHY_THIS_WON } from "@/lib/demo";
import { money } from "@/lib/orkestr-api";
import type { Member, Plan } from "@/lib/orkestr-api";
import s from "./live.module.css";

function prettyTag(tag: string): string {
  return tag.replace(/_/g, " ");
}

// Reasons adapt to the real plan when possible, with a sensible fallback so the
// card always reads well even if the backend returns a different venue.
function buildReasons(plan: Plan): string[] {
  const reasons: string[] = [];
  const tags = plan.venue?.tags ?? [];
  const dietTags = tags.filter((t) => /halal|veg|options|raw/.test(t));
  if (dietTags.length) {
    reasons.push(`Menu covers ${dietTags.map(prettyTag).join(", ")}`);
  }
  if (typeof plan.per_person === "number") {
    reasons.push(`$${plan.per_person} a head lands inside everyone's budget`);
  }
  if (plan.day || plan.time) {
    reasons.push(`${plan.day ?? "FRI"} ${plan.time ?? "19:30"} is the night that works for all five`);
  }
  return reasons.length >= 2 ? reasons : WHY_THIS_WON;
}

export function FinalPlanCard({ plan, members }: { plan: Plan; members: Member[] }) {
  const tags = plan.venue?.tags ?? [];
  const satisfies = plan.satisfies ?? [];
  const happyIds = satisfies.length ? satisfies : members.map((m) => m.id);
  const seats = members.length || 5;
  const reasons = buildReasons(plan);

  return (
    <div className={s.planCard}>
      <div className={s.planHead}>
        <div>
          <span className={s.stageKicker}>The plan</span>
          <h2 className={s.planVenue}>{plan.venue?.name ?? plan.title ?? "Dinner"}</h2>
          <p className={s.planAddr}>{plan.venue?.address ?? "Singapore"}</p>
        </div>
        <StatusPill kind="done">Booked</StatusPill>
      </div>

      <div className={s.planFacts}>
        <Fact label="When" value={`${plan.day ?? "FRI"} · ${plan.time ?? "19:30"}`} />
        <Fact label="Per person" value={money(plan.per_person)} />
        <Fact label="Table" value={`${seats} seats`} />
        <Fact label="Booking" value={plan.booking_ref ?? "confirmed"} />
      </div>

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
          <span className={s.satCount}>{happyIds.length}/5 happy</span>
        </div>
      </div>

      <div className={s.planBlock}>
        <span className={s.blockLabel}>Why this place won</span>
        <ul className={s.whyList}>
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
