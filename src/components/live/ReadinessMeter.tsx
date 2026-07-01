import { ProgressRing } from "./primitives";
import { formatNames } from "@/lib/demo";
import type { Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

export function ReadinessMeter({
  members,
  readyIds,
  size = 64,
}: {
  members: Member[];
  readyIds: string[];
  size?: number;
}) {
  const ready = members.filter((m) => readyIds.includes(m.id));
  const pending = members.filter((m) => !readyIds.includes(m.id));
  const count = ready.length;
  const total = members.length || 5;

  // Compact, human waiting line — never "A and B and C", never a giant list.
  const waitingLine =
    pending.length === 0
      ? "Everyone is in — agents are on it"
      : count === 0
        ? "Waiting for everyone to reply"
        : pending.length > 2
          ? `Waiting for ${pending.length} more friends`
          : `Waiting for ${formatNames(pending.map((p) => p.name))}`;

  return (
    <div className={s.readiness}>
      <ProgressRing value={count} total={total} size={size} stroke={6}>
        <span className={s.ringCount}>
          {count}
          <span className={s.ringTotal}>/{total}</span>
        </span>
      </ProgressRing>
      <div className={s.readinessText}>
        <strong>{count} of {total} friends replied</strong>
        <span>{waitingLine}</span>
      </div>
    </div>
  );
}
