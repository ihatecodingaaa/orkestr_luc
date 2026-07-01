"use client";

import { Avatar } from "./primitives";
import { ActivityFeed } from "./ActivityFeed";
import type { ActivityItem } from "./ActivityFeed";
import { COPY, PREPARING } from "@/lib/demo";
import type { Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

// Small subtext helper so we never claim "waiting" once everyone is in.
function waitingSubtext(count: number, total: number, allIn: boolean): string {
  if (allIn) return `${count} of ${total} replied. ${COPY.agentsWorkingHint}`;
  return `${count} of ${total} replied. ${COPY.agentsStartHint}`;
}

export function WaitingRoom({
  members,
  readyIds,
  nudged,
  onNudge,
  activity,
}: {
  members: Member[];
  readyIds: string[];
  nudged: string[];
  onNudge: (id: string) => void;
  activity: ActivityItem[];
}) {
  const count = readyIds.length;
  const total = members.length || 5;
  const allIn = total > 0 && count >= total;

  return (
    <div className={s.waiting}>
      <div className={s.stageHead}>
        <span className={s.stageKicker}>{allIn ? "Agents working" : "Waiting room"}</span>
        <h2 className={s.stageTitle}>
          {allIn ? "Everyone is in. Agents are working." : "Your agents are waiting for friends"}
        </h2>
        <p className={s.stageSub}>{waitingSubtext(count, total, allIn)}</p>
      </div>

      <div className={s.friendRow}>
        {members.map((m) => {
          const ready = readyIds.includes(m.id);
          const isNudged = nudged.includes(m.id);
          const statusText = ready ? "Ready ✓" : isNudged ? "Nudged — give it a sec" : "Hasn't replied yet";
          return (
            <div key={m.id} className={`${s.friendChip} ${ready ? s.friendReady : ""}`}>
              <Avatar id={m.id} size={38} ring={ready} />
              <div className={s.friendInfo}>
                <strong>{m.name}</strong>
                <span>{statusText}</span>
              </div>
              {!ready ? (
                <button
                  type="button"
                  className={s.nudgeBtn}
                  onClick={() => onNudge(m.id)}
                  disabled={isNudged}
                >
                  {isNudged ? "Nudged" : "Nudge"}
                </button>
              ) : (
                <span className={s.friendCheck} aria-hidden>
                  ✓
                </span>
              )}
            </div>
          );
        })}
      </div>

      <div className={s.prepBlock}>
        <span className={s.prepLabel}>
          {allIn ? "Orkestr is preparing your plan" : "While you wait, Orkestr is preparing"}
        </span>
        <div className={s.prepGrid}>
          {PREPARING.map((p) => (
            <div key={p.title} className={s.prepCard}>
              <span className={s.prepDot} aria-hidden />
              <strong>{p.title}</strong>
              <span>{p.caption}</span>
            </div>
          ))}
        </div>
      </div>

      <ActivityFeed items={activity} />
    </div>
  );
}
