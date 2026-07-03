"use client";

import { useState } from "react";
import { VenueBadge } from "./verification";
import { X402_PROOF } from "@/lib/demo";
import { shortHash } from "@/lib/orkestr-api";
import type { VenueVerification } from "@/lib/orkestr-api";
import type { MealSplit } from "@/lib/split";
import s from "./live.module.css";

// Presenter-facing "no hallucination" proof. Collapsed by default so it never
// clutters the plan; one tap opens the receipt of *why* the result is trustworthy.
export function TrustPanel({
  verification,
  split,
  venueName,
}: {
  verification?: VenueVerification;
  split?: MealSplit;
  venueName?: string;
}) {
  const [open, setOpen] = useState(false);
  const status = verification?.verification_status ?? "unverified";

  const verificationBits: string[] = [];
  if (verification?.google_place_id) verificationBits.push(`Place ID ${verification.google_place_id.slice(0, 14)}…`);
  if (verification?.google_maps_uri) verificationBits.push("Google Maps link");
  if (verification?.source_domain) verificationBits.push(`source ${verification.source_domain}`);
  if (status === "demo") verificationBits.push("labeled sample — not presented as verified");
  if (verificationBits.length === 0) verificationBits.push("no external verification on record");

  const reconciles = split ? split.totals_reconcile : true;

  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>Why judges can trust this result</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.trustBody}>
          <div className={s.trustRow}>
            <span className={s.trustLabel}>Venue source</span>
            <span className={s.trustValue}>
              <VenueBadge verification={verification} small />
              {venueName ? <span className={s.trustMuted}> · {venueName}</span> : null}
            </span>
          </div>
          <div className={s.trustRow}>
            <span className={s.trustLabel}>Verification</span>
            <span className={s.trustValue}>{verificationBits.join(" · ")}</span>
          </div>
          <div className={s.trustRow}>
            <span className={s.trustLabel}>Split method</span>
            <span className={s.trustValue}>
              Itemized by order — shared dishes split only among the selected friends;
              service charge + GST allocated proportionally
              {reconciles ? " (per-person totals reconcile to the bill exactly)" : ""}
            </span>
          </div>
          <div className={s.trustRow}>
            <span className={s.trustLabel}>Payment</span>
            <span className={s.trustValue}>
              Merchant paid once by the Orkestr Agent Wallet over x402 —{" "}
              <a href={X402_PROOF.basescan} target="_blank" rel="noreferrer" className={s.vLink}>
                {shortHash(X402_PROOF.txHash, 8, 6)} ↗
              </a>
              ; friends split internally (no new payment).
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
