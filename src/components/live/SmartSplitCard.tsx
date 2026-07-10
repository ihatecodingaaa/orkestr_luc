"use client";

import { useState } from "react";
import { Avatar } from "./primitives";
import { fmtMoney, splitSourceLabel } from "@/lib/split";
import type { MealSplit, SplitMode } from "@/lib/split";
import type { SplitSource } from "@/lib/useRoom";
import s from "./live.module.css";

// Subtle source-of-truth label. Honest per split_source: real backend orders,
// a session draft, or the local demo preview (never labels canned data as real).
function SplitSourceBadge({ split, source }: { split: MealSplit; source: SplitSource }) {
  return (
    <span className={s.splitSourceBadge} data-source={source}>
      {splitSourceLabel(split, source)}
    </span>
  );
}

function SplitFact({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.splitFact}>
      <span className={s.splitFactLabel}>{label}</span>
      <span className={s.splitFactValue}>{value}</span>
    </div>
  );
}

// The internal settlement, styled as a warm receipt. Replaces the naive equal
// split — each friend pays for what they actually ordered, shared dishes split
// only among the friends who joined them, tax + service allocated fairly.
export function SmartSplitCard({
  split,
  mode,
  onModeChange,
  source = "local",
  showFallbackNote = false,
}: {
  split: MealSplit;
  mode: SplitMode;
  onModeChange?: (m: SplitMode) => void;
  source?: SplitSource;
  // Presenter-only, calm note shown when the backend split was tried and failed.
  showFallbackNote?: boolean;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const toggle = (id: string) => setOpenId((cur) => (cur === id ? null : id));
  const showToggle = typeof onModeChange === "function";

  return (
    <div className={s.splitCard}>
      <div className={s.splitHead}>
        <div>
          <span className={s.stageKicker}>After the meal</span>
          <h3 className={s.splitTitle}>Smart itemized split</h3>
          <SplitSourceBadge split={split} source={source} />
        </div>
        <span className={s.splitTotal}>{fmtMoney(split.total)} total</span>
      </div>

      {showFallbackNote ? (
        <p className={s.splitFallbackNote} role="status">
          Using local demo split. Backend split unavailable.
        </p>
      ) : null}

      {showToggle ? (
        <div className={s.splitToggle} role="tablist" aria-label="Split method">
          {(["itemized", "equal"] as SplitMode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              className={`${s.splitToggleBtn} ${mode === m ? s.splitToggleOn : ""}`}
              onClick={() => onModeChange?.(m)}
            >
              {m === "itemized" ? "Itemized" : "Equal"}
            </button>
          ))}
        </div>
      ) : null}

      <div className={s.splitFacts}>
        <SplitFact label="Subtotal" value={fmtMoney(split.subtotal)} />
        <SplitFact
          label={`Service ${Math.round((split.service_charge_rate ?? 0) * 100)}%`}
          value={fmtMoney(split.service_charge)}
        />
        <SplitFact label={`GST ${Math.round((split.gst_rate ?? 0) * 100)}%`} value={fmtMoney(split.gst)} />
      </div>

      <ul className={s.splitList}>
        {split.per_person.map((p) => {
          const open = openId === p.member_id;
          return (
            <li key={p.member_id} className={s.splitPerson}>
              <button
                type="button"
                className={s.splitPersonHead}
                onClick={() => toggle(p.member_id)}
                aria-expanded={open}
              >
                <Avatar id={p.member_id} size={30} />
                <span className={s.splitName}>{p.name}</span>
                {p.over_budget ? (
                  <span className={s.splitOverPill} title={p.warning}>
                    over budget
                  </span>
                ) : null}
                <span className={s.splitAmt}>{fmtMoney(p.total)}</span>
                <span className={s.splitChevron} aria-hidden>
                  {open ? "−" : "+"}
                </span>
              </button>

              {open ? (
                <div className={s.splitDetail}>
                  <ul className={s.splitItems}>
                    {p.items.length === 0 ? (
                      <li className={s.splitItemEmpty}>No items — nothing to reimburse.</li>
                    ) : (
                      p.items.map((it, i) => (
                        <li key={`${it.id ?? it.name}-${i}`} className={s.splitItem}>
                          <span
                            className={`${s.itemTag} ${it.kind === "shared" ? s.itemTagShared : s.itemTagPersonal}`}
                          >
                            {it.kind}
                          </span>
                          <span className={s.itemName}>
                            {it.name}
                            {it.kind === "shared" && it.shared_count > 1 ? (
                              <em className={s.itemShareNote}> · split {it.shared_count} ways</em>
                            ) : null}
                          </span>
                          <span className={s.itemAmt}>{fmtMoney(it.amount)}</span>
                        </li>
                      ))
                    )}
                  </ul>
                  <div className={s.splitBreakRow}>
                    <span>Service charge</span>
                    <span>{fmtMoney(p.service_charge)}</span>
                  </div>
                  <div className={s.splitBreakRow}>
                    <span>GST</span>
                    <span>{fmtMoney(p.gst)}</span>
                  </div>
                  {p.warning ? (
                    <p className={s.splitWarnText}>
                      <span aria-hidden>⚠ </span>
                      {p.warning}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      <p className={s.splitNote}>
        Orkestr paid the merchant once through x402. Friends reimburse based on what they
        ordered — shared dishes split only among the friends who joined them.
      </p>
    </div>
  );
}
