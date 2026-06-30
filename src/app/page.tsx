"use client";

import { useState } from "react";
import { GroupHub } from "@/components/live/GroupHub";
import { GroupRoom } from "@/components/live/GroupRoom";
import type { DemoGroup } from "@/lib/demo";
import s from "../components/live/live.module.css";

export default function Home() {
  const [group, setGroup] = useState<DemoGroup | null>(null);

  return (
    <main className={`midnight ${s.app}`}>
      <div className={s.appInner}>
        {group ? (
          <GroupRoom key={group.id} group={group} onBack={() => setGroup(null)} />
        ) : (
          <GroupHub onOpenGroup={setGroup} />
        )}
      </div>
    </main>
  );
}
