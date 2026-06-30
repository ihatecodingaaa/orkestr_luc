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

export function PaymentProofCard() {
  const p = X402_PROOF;

  return (
    <div className={s.proofCard}>
      <div className={s.proofHead}>
        <div>
          <span className={s.proofKicker}>Real x402 payment proof</span>
          <h2 className={s.proofTitle}>USDC payment verified</h2>
        </div>
        <StatusPill kind="paid">{p.status}</StatusPill>
      </div>

      <div className={s.proofFlow}>
        <WalletNode label={p.buyerLabel} addr={p.buyer} role="buyer" />
        <div className={s.proofArrow}>
          <span className={s.proofArrowAmt}>{p.amount}</span>
          <span className={s.proofArrowLine} aria-hidden>
            <span className={s.proofArrowCap}>→</span>
          </span>
          <span className={s.proofArrowSub}>via x402</span>
        </div>
        <WalletNode label={p.merchantLabel} addr={p.merchant} role="merchant" />
      </div>

      <div className={s.proofFacts}>
        <Fact label="Payment mode" value={p.paymentMode} />
        <Fact label="Amount" value={`${p.amount} (${p.amountBaseUnits} base units)`} />
        <Fact label="Asset" value={p.asset} />
        <Fact label="Network" value={p.networkId} />
        <Fact label="Chain" value={p.network} />
        <Fact label="Protocol" value={p.protocol} />
      </div>

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

      <p className={s.proofNote}>{COPY.walletExplainer}</p>
      <span className={s.demoTag}>
        Verified demo receipt — this screen never starts a new payment.
      </span>
    </div>
  );
}
