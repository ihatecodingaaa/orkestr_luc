"use client";

import { useState } from "react";
import { VenueBadge } from "./verification";
import { SAVED_RECEIPT } from "@/lib/demo";
import { shortHash } from "@/lib/orkestr-api";
import type { VenueVerification, X402Receipt } from "@/lib/orkestr-api";
import type { MealSplit } from "@/lib/split";
import s from "./live.module.css";

// Presenter-facing "no hallucination" proof. Collapsed by default so it never
// clutters the plan; one tap opens the receipt of *why* the result is trustworthy.
// The payment row adapts to the active receipt: the saved verified fallback, or a
// fresh proof the presenter created live (never claims "just now" for the saved one).
export function TrustPanel({
  verification,
  split,
  venueName,
  receipt = SAVED_RECEIPT,
  awaitingFreshProof = false,
}: {
  verification?: VenueVerification;
  split?: MealSplit;
  venueName?: string;
  receipt?: X402Receipt;
  awaitingFreshProof?: boolean;
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

  // Honest merchant-adapter label — never claims the restaurant "accepted" via an
  // agent unless it is the clearly-labelled sandbox agent.
  const adapterLine =
    status === "verified"
      ? "Google Places–verified place data with a booking handoff (Maps / venue)."
      : status === "source_only"
        ? "Real web source with a booking handoff link."
        : status === "demo"
          ? "Sample venue data (demo) — booking handoff only, no agent claim."
          : "Booking handoff; sandbox merchant agent shown only as a future rail.";

  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>Why this is hard to fake</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.trustBody}>
          <ul className={s.trustPillars} aria-label="What makes this hard to fake">
            {[
              "Real venue evidence",
              "Group agreement",
              "Fair split math",
              "Payment proof",
              "Merchant adapter",
              "Guest-first flow",
            ].map((p) => (
              <li key={p} className={s.trustPillar}>
                <span className={s.trustPillarMark} aria-hidden>
                  ✓
                </span>
                {p}
              </li>
            ))}
          </ul>
          <p className={s.trustMoatLead}>
            Split apps start after the meal. Orkestr starts before the decision and carries through
            to handoff, split, and proof.
          </p>
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
            <span className={s.trustLabel}>Merchant adapter</span>
            <span className={s.trustValue}>
              {adapterLine} Most restaurants don&apos;t run agents yet — Orkestr verifies the place
              and prepares a booking handoff today.
            </span>
          </div>
          <div className={s.trustRow}>
            <span className={s.trustLabel}>Payment</span>
            <span className={s.trustValue}>
              {receipt.isFresh ? (
                <>
                  Fresh x402 testnet receipt — the Orkestr Agent Wallet paid the Merchant Wallet
                  once, live.{" "}
                  <a href={receipt.basescan} target="_blank" rel="noreferrer" className={s.vLink}>
                    {shortHash(receipt.txHash, 8, 6)} ↗
                  </a>
                  {" "}— open Basescan for the live timestamp; friends split internally (no new
                  payment).
                </>
              ) : awaitingFreshProof ? (
                <>
                  Fresh x402 payment not run yet — the presenter can settle a live 0.001 USDC
                  testnet payment on demand. Saved fallback proof:{" "}
                  <a href={receipt.basescan} target="_blank" rel="noreferrer" className={s.vLink}>
                    {shortHash(receipt.txHash, 8, 6)} ↗
                  </a>
                  . Friends never connect a wallet; they split internally.
                </>
              ) : (
                <>
                  Saved verified x402 receipt — the Orkestr Agent Wallet paid the Merchant Wallet
                  once in a previous testnet proof.{" "}
                  <a href={receipt.basescan} target="_blank" rel="noreferrer" className={s.vLink}>
                    {shortHash(receipt.txHash, 8, 6)} ↗
                  </a>
                  {" "}— this screen does not create a new payment; friends split internally.
                </>
              )}
            </span>
          </div>
          <p className={s.trustMoat}>
            <strong>Google finds places. Orkestr gets groups to agree.</strong> General assistants
            can suggest a restaurant; Orkestr coordinates the whole group — constraints, trade-offs,
            verified venue evidence, a fair itemized split, and payment proof — in one shareable room.
          </p>
        </div>
      ) : null}
    </div>
  );
}
