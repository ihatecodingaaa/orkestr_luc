"use client";

import { useState } from "react";
import s from "./live.module.css";

export type DemoAction = {
  label: string;
  hint: string;
  onClick: () => void;
  tone?: "primary" | "ghost";
};

export function DemoControls({ actions, busy }: { actions: DemoAction[]; busy?: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <div className={s.demoDock}>
      {open ? (
        <div className={s.demoPanel} role="dialog" aria-label="Presenter controls">
          <div className={s.demoPanelHead}>
            <span className={s.demoPanelTitle}>Presenter controls</span>
            <button
              type="button"
              className={s.demoClose}
              onClick={() => setOpen(false)}
              aria-label="Hide presenter controls"
            >
              ✕
            </button>
          </div>
          <div className={s.demoActions}>
            {actions.map((a) => (
              <button
                key={a.label}
                type="button"
                className={`${s.demoBtn} ${a.tone === "primary" ? s.demoBtnPrimary : ""}`}
                onClick={a.onClick}
                disabled={busy}
              >
                <strong>{a.label}</strong>
                <span>{a.hint}</span>
              </button>
            ))}
          </div>
          <span className={s.demoSafe}>Demo-safe — never spends real USDC</span>
        </div>
      ) : null}

      <button
        type="button"
        className={s.demoToggle}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={s.demoToggleDot} aria-hidden />
        {open ? "Hide controls" : "Demo controls"}
      </button>
    </div>
  );
}
