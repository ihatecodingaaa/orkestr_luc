"use client";

import type { ReactNode } from "react";
import { Avatar, StatusPill } from "./primitives";
import { DAY_OPTIONS, DIET_OPTIONS, isReadyToSubmit, personaById } from "@/lib/demo";
import type { Constraints, Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={s.field}>
      <span className={s.fieldLabel}>{label}</span>
      {children}
    </div>
  );
}

export function MemberConstraintCard({
  member,
  value,
  onChange,
  onSubmit,
  onEdit,
  submitted,
  submitting,
  editable = true,
}: {
  member: Member;
  value: Constraints;
  onChange: (next: Constraints) => void;
  onSubmit: () => void;
  onEdit: () => void;
  submitted: boolean;
  submitting: boolean;
  editable?: boolean;
}) {
  const persona = personaById(member.id);
  const canSubmit = isReadyToSubmit(value) && !submitting;
  const dietLabels = value.dietary.map(
    (id) => DIET_OPTIONS.find((d) => d.id === id)?.label ?? id,
  );

  return (
    <div className={`${s.memberCard} ${submitted ? s.memberCardReady : ""}`}>
      <div className={s.memberHead}>
        <Avatar id={member.id} size={42} ring={submitted} />
        <div className={s.memberWho}>
          <strong>{member.name}</strong>
          <span>{persona?.wants ?? persona?.vibe ?? "Member"}</span>
        </div>
        <StatusPill kind={submitted ? "ready" : "pending"}>
          {submitted ? "Ready" : "Pending"}
        </StatusPill>
      </div>

      {submitted ? (
        <div className={s.memberSummary}>
          <div className={s.summaryChips}>
            {value.days.map((d) => (
              <span key={d} className={s.miniChip}>
                {d}
              </span>
            ))}
            <span className={s.miniChip}>${value.budget}/head</span>
            {dietLabels.map((d) => (
              <span key={d} className={`${s.miniChip} ${s.miniChipDiet}`}>
                {d}
              </span>
            ))}
          </div>
          {value.freeform ? <p className={s.memberNote}>“{value.freeform}”</p> : null}
          {editable ? (
            <button type="button" className={s.linkBtn} onClick={onEdit} disabled={submitting}>
              Edit
            </button>
          ) : null}
        </div>
      ) : (
        <div className={s.memberForm}>
          <Field label="Days that work">
            <div className={s.chipRow}>
              {DAY_OPTIONS.map((day) => (
                <button
                  key={day}
                  type="button"
                  className={`${s.chip} ${value.days.includes(day) ? s.chipOn : ""}`}
                  onClick={() => onChange({ ...value, days: toggle(value.days, day) })}
                >
                  {day}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Budget / head">
            <div className={s.budgetWrap}>
              <span className={s.budgetSign}>$</span>
              <input
                className={s.budgetInput}
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="60"
                value={value.budget}
                onChange={(e) => onChange({ ...value, budget: e.target.value })}
              />
            </div>
          </Field>

          <Field label="Dietary needs">
            <div className={s.chipRow}>
              {DIET_OPTIONS.map((diet) => (
                <button
                  key={diet.id}
                  type="button"
                  className={`${s.chip} ${value.dietary.includes(diet.id) ? s.chipOn : ""}`}
                  onClick={() => onChange({ ...value, dietary: toggle(value.dietary, diet.id) })}
                >
                  {diet.label}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Anything else">
            <input
              className={s.textInput}
              type="text"
              placeholder="e.g. walkable, group seating"
              value={value.freeform}
              onChange={(e) => onChange({ ...value, freeform: e.target.value })}
            />
          </Field>

          <button
            type="button"
            className={s.btnPrimarySm}
            onClick={onSubmit}
            disabled={!canSubmit}
          >
            {submitting ? "Sending…" : "Submit constraints"}
          </button>
        </div>
      )}
    </div>
  );
}
