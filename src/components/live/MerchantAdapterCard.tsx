"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { VenueVerification } from "@/lib/orkestr-api";
import s from "./live.module.css";

// Booking mode derived HONESTLY from what we actually know about the venue.
// We never claim a restaurant "accepted" via an agent unless it is the sandbox
// merchant agent (clearly labelled). Real restaurants get a booking handoff.
type BookingMode = {
  kind: "google_handoff" | "web_handoff" | "manual" | "none";
  label: string;
  href?: string;
  linkLabel?: string;
};

// A short, mode-specific title so the handoff row reads honestly ("Google Maps
// handoff" / "Website / booking link" / "Manual booking handoff").
function bookingTitle(b: BookingMode): string {
  switch (b.kind) {
    case "google_handoff":
      return "Google Maps handoff";
    case "web_handoff":
      return "Website / booking link";
    default:
      return "Manual booking handoff";
  }
}

function deriveBooking(v?: VenueVerification): BookingMode {
  const status = v?.verification_status;
  const maps = v?.google_maps_uri || undefined;
  const site = v?.website_uri || undefined;
  const source = v?.source_url || undefined;
  if (status === "verified" && (maps || site)) {
    return {
      kind: "google_handoff",
      label: "Booking handoff ready — reserve via Google Maps or the venue directly.",
      href: maps || site,
      linkLabel: maps ? "Open on Google Maps ↗" : "Open venue site ↗",
    };
  }
  if (status === "source_only" && (source || site)) {
    return {
      kind: "web_handoff",
      label: "Booking handoff via the source listing found on the web.",
      href: source || site,
      linkLabel: "Open source ↗",
    };
  }
  if (maps || site || source) {
    return {
      kind: "web_handoff",
      label: "Booking handoff via the venue link.",
      href: maps || site || source,
      linkLabel: "Open link ↗",
    };
  }
  return { kind: "manual", label: "Manual booking handoff — call or walk in." };
}

function Row({
  icon,
  title,
  children,
  tone = "neutral",
}: {
  icon: string;
  title: string;
  children: ReactNode;
  tone?: "ok" | "neutral" | "future";
}) {
  return (
    <div className={`${s.adapterRow} ${tone === "ok" ? s.adapterRowOk : tone === "future" ? s.adapterRowFuture : ""}`}>
      <span className={s.adapterIcon} aria-hidden>
        {icon}
      </span>
      <div className={s.adapterRowText}>
        <strong className={s.adapterRowTitle}>{title}</strong>
        <span className={s.adapterRowBody}>{children}</span>
      </div>
    </div>
  );
}

// The honest merchant-side model. Most restaurants do NOT run agent-commerce
// rails yet, so Orkestr uses a "merchant adapter": verified place data + a real
// booking handoff today, with a clearly-labelled sandbox merchant agent that
// demonstrates the agentic-commerce future. Collapsed by default.
export function MerchantAdapterCard({ verification }: { verification?: VenueVerification }) {
  const [open, setOpen] = useState(false);
  const booking = deriveBooking(verification);
  const status = verification?.verification_status;
  const verifiedLabel =
    status === "verified"
      ? "Verified place data — Google Places"
      : status === "source_only"
        ? "Web source found — Exa discovery"
        : status === "demo"
          ? "Sample venue data (demo)"
          : "Unverified venue";

  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>Merchant adapter</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.adapterBody}>
          <p className={s.adapterIntro}>
            Most restaurants don&apos;t run merchant agents yet. Orkestr uses a merchant adapter
            today — verified place data, source links, and a booking handoff — while the sandbox
            merchant agent shows how this upgrades when agent-commerce rails are adopted.
          </p>

          <div className={s.adapterRows}>
            <span className={s.adapterGroupLabel}>Works today</span>
            <Row icon="✓" title={verifiedLabel} tone={status === "verified" || status === "source_only" ? "ok" : "neutral"}>
              {status === "verified"
                ? "Real place, confirmed by Google Places (place ID + Maps link)."
                : status === "source_only"
                  ? "Real listing found on the web with a source URL."
                  : status === "demo"
                    ? "Sample data — shown only in Demo Mode, never presented as verified."
                    : "No external verification on record."}
            </Row>

            <Row icon="→" title={bookingTitle(booking)} tone="neutral">
              {booking.label}
              {booking.href ? (
                <>
                  {" "}
                  <a className={s.vLink} href={booking.href} target="_blank" rel="noreferrer">
                    {booking.linkLabel}
                  </a>
                </>
              ) : null}
            </Row>

            <span className={`${s.adapterGroupLabel} ${s.adapterGroupFuture}`}>Future rail</span>
            <Row icon="◇" title="Sandbox merchant agent" tone="future">
              Simulation only — an ACP/AP2-style acceptance that demonstrates the future
              agent-commerce rail. Not a claim that this restaurant runs an agent today.
            </Row>
          </div>
        </div>
      ) : null}
    </div>
  );
}
