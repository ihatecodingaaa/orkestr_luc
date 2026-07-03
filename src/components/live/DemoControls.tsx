"use client";

import { useState } from "react";
import { Avatar } from "./primitives";
import s from "./live.module.css";

export type DemoAction = {
  label: string;
  hint?: string;
  onClick: () => void;
  tone?: "primary" | "ghost";
  disabled?: boolean;
};

export type DemoFriend = {
  id: string;
  name: string;
  ready: boolean;
  onSimulate: () => void;
};

export type DemoSection = {
  title: string;
  actions: DemoAction[];
};

// Presenter-only cast controls — runs the whole five-friend story from one laptop.
// Styled as a polished presenter mode, never raw dev tools. Collapsible. Copy is
// mode-aware: "Demo Cast" in Demo Mode, "Presenter tools · Simulation only" in Live.
export function DemoControls({
  friends,
  autoRun,
  sections,
  busy,
  autoRunning,
  title = "Demo Cast",
  subtitle = "Running solo? Simulate friends joining from their phones.",
  toggleLabel = "Demo Cast",
  simulationOnly = false,
}: {
  friends: DemoFriend[];
  autoRun: DemoAction;
  sections: DemoSection[];
  busy?: boolean;
  autoRunning?: boolean;
  title?: string;
  subtitle?: string;
  toggleLabel?: string;
  simulationOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const allIn = friends.length > 0 && friends.every((f) => f.ready);

  return (
    <div className={s.demoDock}>
      {open ? (
        <div className={s.demoPanel} role="dialog" aria-label="Demo Cast presenter controls">
          <div className={s.demoPanelHead}>
            <div>
              <span className={s.demoPanelTitle}>
                {title}
                {simulationOnly ? <span className={s.demoSimTag}>Simulation only</span> : null}
              </span>
              <span className={s.demoPanelSub}>{subtitle}</span>
            </div>
            <button
              type="button"
              className={s.demoClose}
              onClick={() => setOpen(false)}
              aria-label="Hide Demo Cast"
            >
              ✕
            </button>
          </div>

          <div className={s.demoFriends}>
            {friends.map((f) => (
              <button
                key={f.id}
                type="button"
                className={`${s.demoFriend} ${f.ready ? s.demoFriendIn : ""}`}
                onClick={f.onSimulate}
                disabled={busy || f.ready}
                aria-label={f.ready ? `${f.name} is in` : `Simulate ${f.name} submitting`}
              >
                <Avatar id={f.id} size={30} ring={f.ready} />
                <span className={s.demoFriendName}>{f.name}</span>
                <span className={s.demoFriendState}>{f.ready ? "✓ In" : "Submit"}</span>
              </button>
            ))}
          </div>

          <button
            type="button"
            className={s.demoAuto}
            onClick={autoRun.onClick}
            disabled={busy || autoRunning || allIn}
          >
            <strong>{autoRunning ? "Friends replying…" : autoRun.label}</strong>
            <span>{autoRun.hint}</span>
          </button>

          {sections.map((section) => (
            <div key={section.title} className={s.demoSection}>
              <span className={s.demoSectionTitle}>{section.title}</span>
              <div className={s.demoActions}>
                {section.actions.map((a) => (
                  <button
                    key={a.label}
                    type="button"
                    className={`${s.demoBtn} ${a.tone === "primary" ? s.demoBtnPrimary : ""}`}
                    onClick={a.onClick}
                    disabled={busy || a.disabled}
                  >
                    <strong>{a.label}</strong>
                    {a.hint ? <span>{a.hint}</span> : null}
                  </button>
                ))}
              </div>
            </div>
          ))}

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
        {open ? `Hide ${toggleLabel}` : toggleLabel}
      </button>
    </div>
  );
}
