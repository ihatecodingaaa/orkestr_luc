"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { friendColorVar, personInitial } from "@/lib/demo";
import s from "./live.module.css";

// Colored initial chip — no image assets, friend color from the theme.
export function Avatar({
  id,
  size = 36,
  ring = false,
}: {
  id?: string;
  size?: number;
  ring?: boolean;
}) {
  const cv = friendColorVar(id);
  return (
    <span
      className={s.avatar}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.4),
        color: `var(${cv})`,
        background: `color-mix(in srgb, var(${cv}) 22%, transparent)`,
        boxShadow: ring ? `0 0 0 2px color-mix(in srgb, var(${cv}) 70%, transparent)` : "none",
      }}
      aria-hidden
    >
      {personInitial(id)}
    </span>
  );
}

export type PillKind =
  | "live"
  | "ready"
  | "paid"
  | "pending"
  | "active"
  | "done"
  | "info"
  | "warning";

// Status never relies on color alone — there is always a dot + text label.
export function StatusPill({ kind, children }: { kind: PillKind; children: ReactNode }) {
  return (
    <span className={`${s.pill} ${s[`pill_${kind}`]}`}>
      <span className={s.pillDot} aria-hidden />
      {children}
    </span>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — keep the link visible so it can still be copied */
    }
  }

  return (
    <button type="button" className={s.copyBtn} onClick={() => void copy()} aria-live="polite">
      {copied ? "Copied ✓" : label}
    </button>
  );
}

// Circular readiness gauge with center content overlaid.
export function ProgressRing({
  value,
  total,
  size = 76,
  stroke = 7,
  children,
}: {
  value: number;
  total: number;
  size?: number;
  stroke?: number;
  children?: ReactNode;
}) {
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = total > 0 ? Math.min(1, value / total) : 0;

  return (
    <div className={s.ringWrap} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className={s.ringSvg}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--border)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="url(#ork-ring-grad)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className={s.ringFg}
        />
        <defs>
          <linearGradient id="ork-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--primary)" />
            <stop offset="100%" stopColor="var(--primary-2)" />
          </linearGradient>
        </defs>
      </svg>
      <div className={s.ringCenter}>{children}</div>
    </div>
  );
}

// Compact label/value stat used across the plan and proof cards.
export function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={s.fact}>
      <span className={s.factLabel}>{label}</span>
      <span className={s.factValue}>{value}</span>
    </div>
  );
}

// Short on-brand wordmark used in headers.
export function Wordmark({ small = false }: { small?: boolean }) {
  return (
    <span className={`${s.wordmark} ${small ? s.wordmarkSm : ""}`}>
      <span className={s.wordmarkDot} aria-hidden />
      Orkestr
    </span>
  );
}
