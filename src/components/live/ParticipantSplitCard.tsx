import { fmtMoney, splitSourceLabel } from "@/lib/split";
import type { MealSplit } from "@/lib/split";
import type { SplitSource } from "@/lib/useRoom";
import s from "./live.module.css";

// Participant-facing slice of the split: only *your* share, your items, and the
// shared dishes you joined. No wallet, no payment — just what you reimburse.
// Participants get the same subtle source badge but never a scary backend warning.
export function ParticipantSplitCard({
  split,
  memberId,
  myName,
  source = "local",
}: {
  split: MealSplit;
  memberId: string;
  myName?: string;
  source?: SplitSource;
}) {
  const me = split.per_person.find((p) => p.member_id === memberId);
  if (!me) return null;

  return (
    <div className={s.splitCard}>
      <div className={s.splitHead}>
        <div>
          <span className={s.stageKicker}>Your share</span>
          <h3 className={s.splitTitle}>{myName ?? me.name} owes {fmtMoney(me.total)}</h3>
          <span className={s.splitSourceBadge} data-source={source}>
            {splitSourceLabel(split, source)}
          </span>
        </div>
        <span className={s.splitTotal}>{fmtMoney(split.total)} bill</span>
      </div>

      <ul className={s.splitItems}>
        {me.items.length === 0 ? (
          <li className={s.splitItemEmpty}>You didn&apos;t order anything — nothing to pay.</li>
        ) : (
          me.items.map((it, i) => (
            <li key={`${it.id ?? it.name}-${i}`} className={s.splitItem}>
              <span
                className={`${s.itemTag} ${it.kind === "shared" ? s.itemTagShared : s.itemTagPersonal}`}
              >
                {it.kind}
              </span>
              <span className={s.itemName}>
                {it.name}
                {it.kind === "shared" && it.shared_count > 1 ? (
                  <em className={s.itemShareNote}> · split {it.shared_count} ways</em>
                ) : null}
              </span>
              <span className={s.itemAmt}>{fmtMoney(it.amount)}</span>
            </li>
          ))
        )}
      </ul>

      <div className={s.splitBreakRow}>
        <span>Service charge</span>
        <span>{fmtMoney(me.service_charge)}</span>
      </div>
      <div className={s.splitBreakRow}>
        <span>GST</span>
        <span>{fmtMoney(me.gst)}</span>
      </div>
      <div className={`${s.splitBreakRow} ${s.splitBreakTotal}`}>
        <span>Your total</span>
        <span>{fmtMoney(me.total)}</span>
      </div>

      {me.warning ? (
        <p className={s.splitWarnText}>
          <span aria-hidden>⚠ </span>
          {me.warning}
        </p>
      ) : null}

      <div className={s.settleHint}>
        <span className={s.settleHintLabel}>Settle with your usual method</span>
        <div className={s.settleRails}>
          {["PayNow", "Venmo", "Mark as paid"].map((r) => (
            <span key={r} className={s.settleRail}>
              {r}
            </span>
          ))}
        </div>
        <span className={s.settleHintNote}>Common rails — no wallet or crypto needed.</span>
      </div>

      <p className={s.splitNote}>
        You only pay for your own items and the shared dishes you joined. No wallet, no MetaMask,
        no crypto — Orkestr&apos;s Agent Wallet paid the merchant once behind the scenes, and you
        settle your share the way you already do.
      </p>
    </div>
  );
}
