import { GroupCard } from "./GroupCard";
import { Wordmark } from "./primitives";
import { COPY, DEMO_GROUPS } from "@/lib/demo";
import type { DemoGroup } from "@/lib/demo";
import s from "./live.module.css";

function HowChip({ label, text }: { label: string; text: string }) {
  return (
    <span className={s.howChip}>
      <strong>{label}</strong>
      <span>{text}</span>
    </span>
  );
}

export function GroupHub({ onOpenGroup }: { onOpenGroup: (group: DemoGroup) => void }) {
  const primary = DEMO_GROUPS[0];

  return (
    <div className={s.hub}>
      <header className={s.hero}>
        <Wordmark />
        <h1 className={s.heroTitle}>{COPY.heroTitle}</h1>
        <p className={s.heroSub}>{COPY.heroSub}</p>

        <div className={s.heroCtas}>
          <button type="button" className={s.btnPrimary} onClick={() => onOpenGroup(primary)}>
            Open {primary.name}
          </button>
          <button type="button" className={s.btnGhost} onClick={() => onOpenGroup(primary)}>
            Create demo group
          </button>
        </div>

        <div className={s.howRow}>
          <HowChip label="Exa" text="real venue discovery" />
          <HowChip label="ACP" text="merchant agent booking" />
          <HowChip label="x402" text="programmable USDC payment" />
        </div>
      </header>

      <section className={s.groupSection} aria-label="Your dinner groups">
        <div className={s.sectionHead}>
          <h2 className={s.sectionTitle}>Your dinner groups</h2>
          <span className={s.sectionHint}>Pick a room to jump in</span>
        </div>
        <div className={s.groupGrid}>
          {DEMO_GROUPS.map((group) => (
            <GroupCard key={group.id} group={group} onOpen={() => onOpenGroup(group)} />
          ))}
        </div>
      </section>

      <p className={s.walletNote}>{COPY.walletExplainer}</p>
    </div>
  );
}
