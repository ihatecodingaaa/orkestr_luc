"use client";

import { useCallback, useState } from "react";
import { getSettlement, markTransferPaid } from "@/lib/orkestr-api";
import type { Member, Settlement, Transfer } from "@/lib/orkestr-api";
import { fmtMoney } from "@/lib/split";
import s from "./live.module.css";

// Honest friend-to-friend settlement. Friends never hold wallets, so these legs
// are NEVER on-chain: each row is tracked state only — "Alice owes Bob $12.40",
// marked paid when settled by their usual method (PayNow/Venmo/cash). No tx
// hashes, no chain language. The ONLY on-chain payment in the product is the
// merchant x402 receipt (PaymentProofCard), which must never lend its
// credibility to these rows.
function fmtMarkedAt(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function SettlementCard({
  sessionId,
  settlement,
  members,
}: {
  sessionId: string;
  settlement?: Settlement | null;
  members: Member[];
}) {
  // After a mark-as-paid we refetch and keep the fresher transfers as an
  // override, keyed to the prop object it superseded — when the room state
  // itself refetches (new prop identity), the override is discarded. Derived
  // in render, so no state-sync effect is needed.
  const [override, setOverride] = useState<{
    base: Settlement | null | undefined;
    transfers: Transfer[];
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const transfers =
    (override && override.base === settlement ? override.transfers : settlement?.transfers) ?? [];

  const nameOf = useCallback(
    (id?: string) => members.find((m) => m.id === id)?.name ?? id ?? "—",
    [members],
  );

  const markPaid = useCallback(
    async (personId: string) => {
      setBusyId(personId);
      setError(null);
      try {
        await markTransferPaid(sessionId, personId);
        const fresh = await getSettlement(sessionId);
        if (fresh?.transfers) setOverride({ base: settlement, transfers: fresh.transfers });
      } catch {
        setError("Couldn't save that just now — try again in a moment.");
      } finally {
        setBusyId(null);
      }
    },
    [sessionId, settlement],
  );

  if (!transfers.length) return null;

  return (
    <div className={s.splitCard}>
      <div className={s.settleHead}>
        <div>
          <span className={s.stageKicker}>Friend settlements</span>
          <h3 className={s.settleTitle}>Who pays who back</h3>
        </div>
      </div>
      <p className={s.settleNote}>
        Tracked by Orkestr, settled by your usual method — PayNow, Venmo, or cash. These are
        not blockchain transactions; the only on-chain payment is the merchant&apos;s x402
        testnet receipt above.
      </p>
      <ul className={s.settleList}>
        {transfers.map((t, i) => {
          const paid = t.status === "marked_paid";
          const markedAt = fmtMarkedAt(t.marked_at);
          return (
            <li key={`${t.from}-${t.to}-${i}`} className={s.settleRow}>
              <span className={s.settleName}>
                {nameOf(t.from)} {paid ? "paid" : "owes"} {nameOf(t.to)}
              </span>
              <span className={s.settleAmt}>{fmtMoney(t.amount ?? 0)}</span>
              {paid ? (
                <span className={s.muted}>
                  Marked paid by {nameOf(t.marked_by)}
                  {markedAt ? ` · ${markedAt}` : ""}
                </span>
              ) : (
                <button
                  type="button"
                  className={s.btnPrimarySm}
                  onClick={() => t.from && void markPaid(t.from)}
                  disabled={busyId === t.from}
                >
                  {busyId === t.from ? "Saving…" : "Mark as paid"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error ? (
        <p className={s.splitFallbackNote} role="status">
          {error}
        </p>
      ) : null}
    </div>
  );
}
