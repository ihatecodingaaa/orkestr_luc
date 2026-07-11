"use client";

import { useState } from "react";
import { CopyButton, Fact, StatusPill } from "./primitives";
import { COPY, SAVED_RECEIPT, X402_PROOF } from "@/lib/demo";
import { shortHash } from "@/lib/orkestr-api";
import type { X402Receipt } from "@/lib/orkestr-api";
import s from "./live.module.css";

// Render a receipt's ISO timestamp as a human, local time. Safe on bad/empty
// input (returns null) — only ever shown for a fresh receipt that carries one.
function formatReceiptTime(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString();
}

function WalletNode({
  label,
  addr,
  role,
}: {
  label: string;
  addr: string;
  role: "buyer" | "merchant";
}) {
  return (
    <div className={`${s.walletNode} ${role === "buyer" ? s.walletBuyer : s.walletMerchant}`}>
      <span className={s.walletIcon} aria-hidden>
        {role === "buyer" ? "◎" : "▣"}
      </span>
      <strong className={s.walletLabel}>{label}</strong>
      <code className={s.walletAddr}>{shortHash(addr, 6, 4)}</code>
    </div>
  );
}

// Live Mode, before any fresh proof: the saved receipt is NOT the live proof, so
// the card shows a "not run yet" state and demotes the saved receipt to a small
// secondary "saved fallback proof". Never presents an old tx as the live result.
function PendingProofCard({ saved }: { saved: X402Receipt }) {
  return (
    <div className={`${s.proofCard} ${s.proofPending}`}>
      <div className={s.proofHead}>
        <div>
          <span className={s.proofKicker}>Live x402 payment proof</span>
          <h2 className={s.proofTitle}>Fresh x402 payment not run yet</h2>
        </div>
        <StatusPill kind="pending">Not run</StatusPill>
      </div>
      <p className={s.proofNote}>
        The presenter can run a secret-gated 0.001 USDC testnet payment when ready. It settles on
        Base Sepolia and appears here as the live proof.
      </p>
      <div className={s.proofSecondary}>
        <span className={s.proofSecondaryLabel}>Saved fallback proof</span>
        <span className={s.proofSecondaryBody}>
          Previously settled testnet transaction —{" "}
          <code className={s.proofSecondaryHash}>{shortHash(saved.txHash, 8, 6)}</code>
          <a className={s.proofLink} href={saved.basescan} target="_blank" rel="noreferrer">
            Basescan ↗
          </a>
        </span>
      </div>
    </div>
  );
}

// The WOW card, styled as a premium receipt. First glance stays human — what was
// paid, from whom to whom, and the tx you can verify. The protocol-level detail
// is tucked into a collapsible so it never reads like a blockchain explorer.
//
// `receipt` defaults to the saved verified fallback. When the presenter runs a
// fresh proof, the parent passes the fresh receipt in and the card clearly
// relabels itself ("Fresh x402 testnet receipt" + a "Fresh proof" badge) and
// swaps to the new tx hash / Basescan link — never pretending the saved receipt
// happened "just now". In Live Mode, `awaitingFreshProof` shows the "not run yet"
// state until a fresh proof is created.
export function PaymentProofCard({
  receipt = SAVED_RECEIPT,
  awaitingFreshProof = false,
}: {
  receipt?: X402Receipt;
  awaitingFreshProof?: boolean;
}) {
  const [showTech, setShowTech] = useState(false);
  const fresh = receipt.isFresh;

  // Live Mode, no fresh proof yet — never show the old saved tx as the live proof.
  if (awaitingFreshProof && !fresh) {
    return <PendingProofCard saved={receipt} />;
  }

  const kicker = fresh ? "Fresh x402 testnet receipt" : "Saved verified receipt";
  // Show the settlement timestamp on a fresh receipt (req: fresh proof carries a
  // real time). Falls back to "just now" only if the backend omitted created_at.
  const freshWhen = fresh && receipt.createdAt ? formatReceiptTime(receipt.createdAt) : null;
  const tag = fresh
    ? freshWhen
      ? `Fresh receipt settled ${freshWhen}. Open Basescan to verify on-chain.`
      : "Fresh receipt created just now. Open Basescan for the exact on-chain timestamp."
    : "Previously settled on Base Sepolia. This screen does not create a new payment.";

  return (
    <div className={s.proofCard}>
      <div className={s.proofHead}>
        <div>
          <span className={s.proofKicker}>{kicker}</span>
          <h2 className={s.proofTitle}>{receipt.amount} paid on Base</h2>
        </div>
        <div className={s.proofHeadPills}>
          {fresh ? <span className={s.freshBadge}>Fresh proof</span> : null}
          <StatusPill kind="paid">Paid</StatusPill>
        </div>
      </div>

      <div className={s.proofFlow}>
        <WalletNode label="Orkestr Agent Wallet" addr={receipt.buyer} role="buyer" />
        <div className={s.proofArrow}>
          <span className={s.proofArrowAmt}>{receipt.amount}</span>
          <span className={s.proofArrowLine} aria-hidden>
            <span className={s.proofArrowCap}>→</span>
          </span>
          <span className={s.proofArrowSub}>paid once</span>
        </div>
        <WalletNode label="Merchant Wallet" addr={receipt.merchant} role="merchant" />
      </div>

      <div className={s.proofTx}>
        <div className={s.proofTxRow}>
          <span className={s.proofTxLabel}>Transaction</span>
          <code className={s.proofTxHash}>{shortHash(receipt.txHash, 12, 10)}</code>
        </div>
        <div className={s.proofTxActions}>
          <CopyButton value={receipt.txHash} label="Copy tx hash" />
          <a className={s.proofLink} href={receipt.basescan} target="_blank" rel="noreferrer">
            View on Basescan ↗
          </a>
        </div>
      </div>

      <div className={s.proofTech}>
        <button
          type="button"
          className={s.proofTechToggle}
          onClick={() => setShowTech((o) => !o)}
          aria-expanded={showTech}
        >
          <span>Technical details</span>
          <span className={s.proofTechChevron} aria-hidden>
            {showTech ? "Hide" : "Show"}
          </span>
        </button>
        {showTech ? (
          <div className={s.proofTechBody}>
            <ul className={s.proofChecks}>
              {X402_PROOF.verified.map((v) => (
                <li key={v}>
                  <span className={s.proofCheckMark} aria-hidden>
                    ✓
                  </span>
                  {v}
                </li>
              ))}
            </ul>
            <div className={s.proofFacts}>
              <Fact label="Payment mode" value={receipt.paymentMode} />
              <Fact label="Amount" value={`${receipt.amount} (${receipt.amountBaseUnits} base units)`} />
              <Fact label="Asset" value={receipt.asset} />
              <Fact label="Network" value={receipt.networkId} />
              <Fact label="Chain" value={receipt.network} />
              <Fact label="Receipt" value={fresh ? "Fresh demo receipt" : "Saved verified receipt"} />
              {freshWhen ? <Fact label="Settled at" value={freshWhen} /> : null}
            </div>
          </div>
        ) : null}
      </div>

      <p className={s.proofNote}>{COPY.walletExplainer}</p>
      <span className={s.demoTag}>{tag}</span>
    </div>
  );
}
