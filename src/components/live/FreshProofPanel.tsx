"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getDemoReceiptStatus, runDemoReceipt, toReceipt } from "@/lib/orkestr-api";
import type { DemoReceiptStatus, X402Receipt } from "@/lib/orkestr-api";
import s from "./live.module.css";

const CONFIRM_PHRASE = "PAY_0.001_USDC";

type Phase = "idle" | "loading" | "success" | "error";

// Presenter-only, collapsed by default. The ONE deliberate control that can
// create a real Base-Sepolia USDC transaction during judging. Disabled by
// default on the backend and gated by a secret + exact confirm phrase.
//
// Safety: the secret lives ONLY in this component's state — never localStorage,
// never sessionStorage, never the URL, never the console. It is sent solely to
// the run endpoint. Nothing here auto-runs: no mount trigger, no polling
// trigger, no retry. Only a click with a valid secret + exact phrase fires it.
export function FreshProofPanel({ onFresh }: { onFresh: (r: X402Receipt) => void }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<DemoReceiptStatus | null>(null);
  const [statusLoaded, setStatusLoaded] = useState(false);
  const [secret, setSecret] = useState("");
  const [confirm, setConfirm] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState("");
  const [cooldownLeft, setCooldownLeft] = useState(0);
  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  // Read-only status probe. Never triggers a payment — safe on mount / refresh.
  const refreshStatus = useCallback(async () => {
    try {
      const st = await getDemoReceiptStatus();
      if (!aliveRef.current) return;
      setStatus(st);
      setStatusLoaded(true);
      setCooldownLeft(st.cooldown_remaining_seconds ?? 0);
    } catch {
      if (!aliveRef.current) return;
      // Backend unreachable or endpoint absent — treat as disabled, stay calm.
      setStatus(null);
      setStatusLoaded(true);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    // Deferred (not a synchronous setState in the effect body) — the status
    // read is payment-safe and only sets state once it resolves.
    const kick = window.setTimeout(() => void refreshStatus(), 0);
    return () => {
      aliveRef.current = false;
      window.clearTimeout(kick);
    };
  }, [refreshStatus]);

  // Local cooldown countdown — purely visual; never calls the run endpoint.
  useEffect(() => {
    if (cooldownLeft <= 0) return;
    const id = window.setInterval(() => {
      setCooldownLeft((n) => (n > 0 ? n - 1 : 0));
    }, 1000);
    return () => window.clearInterval(id);
  }, [cooldownLeft]);

  const enabled = Boolean(status?.enabled);
  const phrase = status?.confirm_phrase ?? CONFIRM_PHRASE;
  const confirmOk = confirm === phrase;
  const cooling = cooldownLeft > 0;
  const canRun =
    enabled && !cooling && phase !== "loading" && confirmOk && secret.trim().length > 0;

  const run = useCallback(async () => {
    // Belt-and-braces double-click guard (the button is also disabled in flight).
    if (!canRun || inFlightRef.current) return;
    inFlightRef.current = true;
    setPhase("loading");
    setMessage("Sending 0.001 USDC through x402…");
    try {
      const res = await runDemoReceipt(secret, confirm);
      if (!aliveRef.current) return;
      if (res.status === 200 && res.ok) {
        const fresh = toReceipt(res.body);
        if (fresh) onFresh(fresh);
        setPhase("success");
        setMessage("Fresh receipt created.");
        setSecret(""); // do not keep the secret around after use
        setConfirm("");
        void refreshStatus(); // pick up the cooldown that just started
      } else if (res.status === 429) {
        const left = Number(res.body?.cooldown_remaining_seconds) || 0;
        setCooldownLeft(left);
        setPhase("error");
        setMessage(left > 0 ? `Cooldown active. Try again in ${left}s.` : "Cooldown active.");
      } else if (res.status === 409) {
        setPhase("error");
        setMessage("A run is already in progress. Please wait for it to finish.");
      } else if (res.status === 403) {
        setPhase("error");
        setMessage("Unauthorized — check the presenter secret and try again.");
      } else if (res.status === 400) {
        setPhase("error");
        setMessage(`Type the exact confirm phrase: ${phrase}`);
      } else if (res.status === 404) {
        setPhase("error");
        setMessage("Fresh proof runs are disabled on the backend.");
        void refreshStatus();
      } else {
        setPhase("error");
        setMessage("Fresh proof did not settle. Nothing was charged twice — check server logs.");
      }
    } catch {
      if (!aliveRef.current) return;
      setPhase("error");
      setMessage("Could not reach the backend. No payment was sent.");
    } finally {
      inFlightRef.current = false;
    }
  }, [canRun, secret, confirm, phrase, onFresh, refreshStatus]);

  // Status line under the controls — calm, never a raw stack trace.
  const statusLine = (() => {
    if (!statusLoaded) return "Checking backend…";
    if (!enabled) return "Fresh proof runs are disabled on the backend.";
    if (phase === "loading") return "Sending 0.001 USDC through x402…";
    if (cooling) return `Cooldown active. Try again in ${cooldownLeft}s.`;
    if (phase === "success") return message || "Fresh receipt created.";
    if (phase === "error") return message;
    return "Ready to create a fresh 0.001 USDC testnet receipt.";
  })();

  const tone = !enabled
    ? s.freshDisabled
    : phase === "success"
      ? s.freshOk
      : phase === "error"
        ? s.freshErr
        : s.freshReady;

  return (
    <div className={s.howto}>
      <button
        type="button"
        className={s.howtoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.howtoTitle}>
          Fresh x402 proof run
          {enabled ? <span className={s.freshLive}> · live</span> : null}
        </span>
        <span className={s.howtoChevron} aria-hidden>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? (
        <div className={s.freshBody}>
          <p className={s.freshIntro}>
            Run once during judging to create a new Base Sepolia USDC transaction. Disabled by
            default and requires a secret. Everything else in the demo stays mock-safe — this is the
            only control that can spend testnet USDC.
          </p>

          <div className={s.freshForm}>
            <label className={s.freshField}>
              <span className={s.freshLabel}>Presenter secret</span>
              <input
                className={s.freshInput}
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="X402_DEMO_RECEIPT_SECRET"
                autoComplete="off"
                spellCheck={false}
                disabled={!enabled || phase === "loading"}
              />
            </label>
            <label className={s.freshField}>
              <span className={s.freshLabel}>
                Type <code className={s.freshCode}>{phrase}</code> to confirm
              </span>
              <input
                className={s.freshInput}
                type="text"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={phrase}
                autoComplete="off"
                spellCheck={false}
                disabled={!enabled || phase === "loading"}
              />
            </label>
          </div>

          <div className={s.freshActions}>
            <button
              type="button"
              className={s.freshBtn}
              onClick={() => void run()}
              disabled={!canRun}
            >
              {phase === "loading" ? "Sending…" : "Run fresh x402 proof"}
            </button>
            <span className={`${s.freshStatus} ${tone}`} role="status" aria-live="polite">
              {statusLine}
            </span>
          </div>

          <p className={s.freshFoot}>
            The secret is sent only to the run endpoint — never stored, never logged. A cooldown and
            an in-flight lock prevent accidental double runs.
          </p>
        </div>
      ) : null}
    </div>
  );
}
