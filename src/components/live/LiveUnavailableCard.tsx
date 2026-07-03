"use client";

import { useState } from "react";
import { Wordmark } from "./primitives";
import { APP_MODE, CONFIGURED_API_URL, ENABLE_LIVE_PIPELINE, PRESENTER_TOOLS_VISIBLE } from "@/lib/appMode";
import s from "./live.module.css";

// Live Mode blocking state. When the backend can't be reached, Live Mode does NOT
// silently fall back to a sample room — it blocks here with a real error and a
// Retry, so nothing fake is ever shown as the live result. Participants see only
// the calm copy; the technical diagnostics are presenter-only.
export function LiveUnavailableCard({
  onRetry,
  retrying,
  failedEndpoint,
  statusText,
}: {
  onRetry: () => void;
  retrying?: boolean;
  failedEndpoint?: string;
  statusText?: string;
}) {
  const [showDiag, setShowDiag] = useState(false);

  return (
    <div className={s.blockCard} role="alert">
      <Wordmark />
      <div className={s.blockIcon} aria-hidden>
        ⚡
      </div>
      <h2 className={s.blockTitle}>Live backend unavailable</h2>
      <p className={s.blockBody}>
        Orkestr needs the live backend to create a real room, verify restaurants, calculate the
        split, and prepare payment proof.
      </p>
      <div className={s.blockActions}>
        <button type="button" className={s.blockRetry} onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Retry"}
        </button>
      </div>

      {PRESENTER_TOOLS_VISIBLE ? (
        <div className={s.blockDiag}>
          <button
            type="button"
            className={s.blockDiagToggle}
            onClick={() => setShowDiag((o) => !o)}
            aria-expanded={showDiag}
          >
            {showDiag ? "Hide diagnostics" : "Presenter diagnostics"}
          </button>
          {showDiag ? (
            <dl className={s.blockDiagGrid}>
              <div>
                <dt>App mode</dt>
                <dd>{APP_MODE}</dd>
              </div>
              <div>
                <dt>Backend URL</dt>
                <dd className={s.blockDiagMono}>{CONFIGURED_API_URL || "(unset — localhost default)"}</dd>
              </div>
              <div>
                <dt>Failed endpoint</dt>
                <dd className={s.blockDiagMono}>{failedEndpoint ?? "createLiveSession / session sync"}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{statusText ?? "unreachable"}</dd>
              </div>
              <div>
                <dt>Live pipeline</dt>
                <dd>{ENABLE_LIVE_PIPELINE ? "on" : "off"}</dd>
              </div>
            </dl>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
