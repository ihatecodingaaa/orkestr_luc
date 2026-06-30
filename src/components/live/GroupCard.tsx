import type { CSSProperties } from "react";
import { Avatar, StatusPill } from "./primitives";
import type { PillKind } from "./primitives";
import { PERSONAS } from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import s from "./live.module.css";

const MODE_PILL: Record<DemoGroup["mode"], PillKind> = {
  live: "live",
  ready: "ready",
  paid: "paid",
};

export function GroupCard({ group, onOpen }: { group: DemoGroup; onOpen: () => void }) {
  const avatars = PERSONAS.slice(0, Math.min(group.memberCount, 5));

  return (
    <button
      type="button"
      className={s.groupCard}
      onClick={onOpen}
      style={{ "--accent": `var(${group.accentVar})` } as CSSProperties}
    >
      <div className={s.groupCardTop}>
        <span className={s.groupEmoji} aria-hidden>
          {group.emoji}
        </span>
        <StatusPill kind={MODE_PILL[group.mode]}>{group.status}</StatusPill>
      </div>

      <h3 className={s.groupName}>{group.name}</h3>
      <p className={s.groupTagline}>{group.tagline}</p>

      <div className={s.groupCardBottom}>
        <div className={s.avatarStack}>
          {avatars.map((p) => (
            <Avatar key={p.id} id={p.id} size={28} />
          ))}
          <span className={s.memberCount}>{group.memberCount} people</span>
        </div>
      </div>

      <div className={s.groupFoot}>
        <span className={s.groupNext}>{group.nextPlan}</span>
        <span className={s.groupOpen} aria-hidden>
          Open room →
        </span>
      </div>
    </button>
  );
}
