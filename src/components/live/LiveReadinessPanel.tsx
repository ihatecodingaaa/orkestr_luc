"use client";

import { useState } from "react";
import { APP_MODE, API_URL_CONFIGURED } from "@/lib/appMode";
import type { DemoReceiptStatus, VenueVerification } from "@/lib/orkestr-api";
import type { SplitSource } from "@/lib/useRoom";
import s from "./live.module.css";

type RowTone = "ok" | "warn" | "muted";

function DiagRow({ label, value, tone = "muted" }: { label: string; value: string; tone?: RowTone }) {
  return (
    <div className={s.diagRow}>
      <span className={s.diagLabel}>{label}</span>
      <span className={`${s.diagValue} ${tone === "ok" ? s.diagOk : tone === "warn" ? s.diagWarn : ""}`}>
        <span className={s.diagDot} aria-hidden />
        {value}
      </span>
    </div>
  );
}

function venueMode(v?: VenueVerification): { value: string; tone: RowTone } {
  switch (v?.verification_status) {
    case "verified":
      return { value: "Google Places verified", tone: "ok" };
    case "source_only":
      return { value: "Web source (Exa)", tone: "ok" };
    case "demo":
      return { value: "Sample demo venue", tone: "warn" };
    default:
      return { value: "Unverified / none", tone: "warn" };
  }
}

function freshMode(st?: DemoReceiptStatus | null): { value: string; tone: RowTone } {
  if (!st) return { value: "Unknown (backend unreachable)", tone: "warn" };
  if (!st.enabled) return { value: "Disabled on backend", tone: "muted" };
  if ((st.cooldown_remaining_seconds ?? 0) > 0)
    return { value: `Cooling down ${st.cooldown_remaining_seconds}s`, tone: "warn" };
  return { value: st.has_latest_fresh_receipt ? "Enabled · fresh receipt exists" : "Enabled · ready", tone: "ok" };
}

// Presenter-only "Live readiness" diagnostics. Composed entirely from public,
// non-secret status the frontend already has — never a key, token, or secret.
export function LiveReadinessPanel({
  backendConnected,
  verification,
  freshStatus,
  splitSource,
  bookingMode,
}: {
  backendConnected: boolean;
  verification?: VenueVerification;
  freshStatus?: DemoReceiptStatus | null;
  splitSource: SplitSource;
  bookingMode: string;
}) {
  const [open, setOpen] = useState(false);
  const vm = venueMode(verification);
  const fm = freshMode(freshStatus);

  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>Live readiness</span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.diagBody}>
          <DiagRow label="Frontend mode" value={APP_MODE === "live" ? "Live" : "Demo"} tone={APP_MODE === "live" ? "ok" : "muted"} />
          <DiagRow
            label="API URL"
            value={API_URL_CONFIGURED ? "Configured" : "Missing (localhost default)"}
            tone={API_URL_CONFIGURED ? "ok" : "warn"}
          />
          <DiagRow
            label="Backend sync"
            value={backendConnected ? "Connected" : "Unreachable"}
            tone={backendConnected ? "ok" : "warn"}
          />
          <DiagRow label="Venue mode" value={vm.value} tone={vm.tone} />
          <DiagRow label="Merchant adapter" value={bookingMode} tone="muted" />
          <DiagRow
            label="Split source"
            value={splitSource === "backend" ? "Backend (calculated)" : "Local fallback"}
            tone={splitSource === "backend" ? "ok" : "warn"}
          />
          <DiagRow label="Fresh x402 proof" value={fm.value} tone={fm.tone} />
          <p className={s.diagFoot}>Public status only — no keys, tokens, or secrets are shown here.</p>
        </div>
      ) : null}
    </div>
  );
}
