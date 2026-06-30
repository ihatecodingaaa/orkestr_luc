import { Avatar, StatusPill } from "./primitives";
import { money } from "@/lib/orkestr-api";
import type { Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

// Secondary story: the single x402 payment goes Orkestr-agent -> merchant. The
// friends then reconcile their per-head share *inside* the app — which is why
// there is only one buyer wallet and one merchant wallet on-chain.
export function SettlementCard({
  perPerson,
  members,
}: {
  perPerson: number;
  members: Member[];
}) {
  const seats = members.length || 5;
  const share = perPerson > 0 ? perPerson : 80;
  const total = share * seats;

  return (
    <div className={s.settleCard}>
      <div className={s.settleHead}>
        <div>
          <span className={s.stageKicker}>After the meal</span>
          <h3 className={s.settleTitle}>Friends split the bill internally</h3>
        </div>
        <span className={s.settleTotal}>{money(total)} total</span>
      </div>

      <p className={s.settleNote}>
        {"Orkestr's agent paid the merchant once through x402. Each friend just settles their share inside the app — no extra wallets, no on-chain fees."}
      </p>

      <ul className={s.settleList}>
        {members.map((m) => (
          <li key={m.id} className={s.settleRow}>
            <Avatar id={m.id} size={28} />
            <span className={s.settleName}>{m.name}</span>
            <span className={s.settleAmt}>{money(share)}</span>
            <StatusPill kind="ready">Square</StatusPill>
          </li>
        ))}
      </ul>
    </div>
  );
}
