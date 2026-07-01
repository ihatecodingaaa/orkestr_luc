"use client";

import { useState } from "react";
import { CopyButton, Fact, StatusPill } from "./primitives";
import { COPY, X402_PROOF } from "@/lib/demo";
import { shortHash } from "@/lib/orkestr-api";
import s from "./live.module.css";

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

// The WOW card, styled as a premium receipt. First glance stays human — what was
// paid, from whom to whom, and the tx you can verify. The protocol-level detail
// (asset address, network id, base units, verification steps) is tucked into a
// collapsible so it never reads like a blockchain explorer.
export function PaymentProofCard() {
  const p = X402_PROOF;
  const [showTech, setShowTech] = useState(false);

  return (
    <div className={s.proofCard}>
      <div className={s.proofHead}>
        <div>
          <span className={s.proofKicker}>Verified payment receipt</span>
          <h2 className={s.proofTitle}>{p.amount} paid on Base</h2>
        </div>
        <StatusPill kind="paid">{p.status}</StatusPill>
      </div>

      <div className={s.proofFlow}>
        <WalletNode label="Orkestr Agent Wallet" addr={p.buyer} role="buyer" />
        <div className={s.proofArrow}>
          <span className={s.proofArrowAmt}>{p.amount}</span>
          <span className={s.proofArrowLine} aria-hidden>
            <span className={s.proofArrowCap}>→</span>
          </span>
          <span className={s.proofArrowSub}>paid once</span>
        </div>
        <WalletNode label="Merchant Wallet" addr={p.merchant} role="merchant" />
      </div>

      <div className={s.proofTx}>
        <div className={s.proofTxRow}>
          <span className={s.proofTxLabel}>Transaction</span>
          <code className={s.proofTxHash}>{shortHash(p.txHash, 12, 10)}</code>
        </div>
        <div className={s.proofTxActions}>
          <CopyButton value={p.txHash} label="Copy tx hash" />
          <a className={s.proofLink} href={p.basescan} target="_blank" rel="noreferrer">
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
              {p.verified.map((v) => (
                <li key={v}>
                  <span className={s.proofCheckMark} aria-hidden>
                    ✓
                  </span>
                  {v}
                </li>
              ))}
            </ul>
            <div className={s.proofFacts}>
              <Fact label="Payment mode" value={p.paymentMode} />
              <Fact label="Amount" value={`${p.amount} (${p.amountBaseUnits} base units)`} />
              <Fact label="Asset" value={p.asset} />
              <Fact label="Network" value={p.networkId} />
              <Fact label="Chain" value={p.network} />
              <Fact label="Protocol" value={p.protocol} />
            </div>
          </div>
        ) : null}
      </div>

      <p className={s.proofNote}>{COPY.walletExplainer}</p>
      <span className={s.demoTag}>
        Verified demo receipt — this screen never starts a new payment.
      </span>
    </div>
  );
}
