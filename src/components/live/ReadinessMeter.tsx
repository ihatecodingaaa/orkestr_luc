import { ProgressRing } from "./primitives";
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

  const waitingLine =
    pending.length === 0
      ? "Everyone is in — agents are on it"
      : `Waiting for ${pending.map((p) => p.name).join(" and ")}`;

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
