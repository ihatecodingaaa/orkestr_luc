"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { GroupHub } from "@/components/live/GroupHub";
import { GroupRoom } from "@/components/live/GroupRoom";
import { ParticipantView } from "@/components/live/ParticipantView";
import { Wordmark } from "@/components/live/primitives";
import { DEMO_GROUPS } from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import s from "../components/live/live.module.css";

// Option A routing — query params on the existing page:
//   /                              → Group Hub
//   /?room=ORK-XXXX                → Presenter Command Center (shareable)
//   /?room=ORK-XXXX&member=P-001   → Alice's mobile participant view
function LiveApp() {
  const params = useSearchParams();
  const router = useRouter();
  const room = params.get("room");
  const member = params.get("member");

  // Local hub ↔ room navigation for groups opened from the hub (no URL yet).
  const [group, setGroup] = useState<DemoGroup | null>(null);

  // A friend opened their personal link → phone-first participant experience.
  if (room && member) {
    return <ParticipantView roomId={room} memberId={member} />;
  }

  // Presenter command center bound to a shared room id (refresh / link-safe).
  if (room) {
    return (
      <GroupRoom group={DEMO_GROUPS[0]} boundRoomId={room} onBack={() => router.push("/")} />
    );
  }

  return group ? (
    <GroupRoom key={group.id} group={group} onBack={() => setGroup(null)} />
  ) : (
    <GroupHub onOpenGroup={setGroup} />
  );
}

export default function Home() {
  return (
    <main className={`garden ${s.app}`}>
      <div className={s.appInner}>
        <Suspense
          fallback={
            <div className={s.bootFallback}>
              <Wordmark />
            </div>
          }
        >
          <LiveApp />
        </Suspense>
      </div>
    </main>
  );
}
