import { StatusPill, Wordmark } from "./primitives";
import type { PillKind } from "./primitives";
import { ReadinessMeter } from "./ReadinessMeter";
import type { DemoGroup } from "@/lib/demo";
import type { Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

export function GroupRoomHeader({
  group,
  sessionId,
  members,
  readyIds,
  phaseLabel,
  phaseKind,
  onBack,
}: {
  group: DemoGroup;
  sessionId: string | null;
  members: Member[];
  readyIds: string[];
  phaseLabel: string;
  phaseKind: PillKind;
  onBack: () => void;
}) {
  const roomLabel = sessionId ? `Room ${sessionId}` : "Demo room";

  return (
    <header className={s.roomHeader}>
      <div className={s.roomHeaderTop}>
        <button type="button" className={s.backBtn} onClick={onBack}>
          ← All groups
        </button>
        <Wordmark small />
      </div>

      <div className={s.roomHeaderMain}>
        <div className={s.roomTitleBlock}>
          <span className={s.roomEmoji} aria-hidden>
            {group.emoji}
          </span>
          <div>
            <h1 className={s.roomTitle}>{group.name}</h1>
            <div className={s.roomMeta}>
              <span className={s.roomCode}>{roomLabel}</span>
              <StatusPill kind={phaseKind}>{phaseLabel}</StatusPill>
            </div>
          </div>
        </div>

        <ReadinessMeter members={members} readyIds={readyIds} />
      </div>
    </header>
  );
}
