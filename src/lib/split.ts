// Smart itemized meal split — the offline-safe mirror of core/meal_split.py.
//
// Orkestr's agent wallet pays the merchant once over x402; this computes the
// internal settlement: each friend pays for what they actually ordered, shared
// dishes are split only among the friends who joined them, and service charge +
// GST are allocated proportionally. Money is computed in integer cents with
// largest-remainder rounding, so per-person totals ALWAYS sum to the bill total.
//
// Kept byte-for-byte consistent with the Python calculator so the backend
// /api/split endpoint and this UI produce identical numbers.

export type SplitMode = "itemized" | "equal";

export type SplitItemInput = {
  id: string;
  name: string;
  price: number;
  quantity: number;
  assigned_to: string[];
  shared_by: string[];
  category: "personal" | "shared";
};

export type SplitMemberInput = { member_id: string; name: string; budget?: number };

export type PersonItem = {
  id?: string;
  name: string;
  amount: number;
  line_total: number;
  quantity: number;
  kind: "personal" | "shared";
  shared_with: string[];
  shared_count: number;
};

export type PerPerson = {
  member_id: string;
  name: string;
  items_subtotal: number;
  service_charge: number;
  gst: number;
  total: number;
  status: string;
  items: PersonItem[];
  shared_items: PersonItem[];
  budget?: number;
  over_budget?: boolean;
  over_by?: number;
  warning?: string;
};

export type MealSplit = {
  currency: string;
  mode: SplitMode;
  service_charge_rate: number;
  gst_rate: number;
  subtotal: number;
  service_charge: number;
  gst: number;
  total: number;
  rounding: number;
  items: SplitItemInput[];
  per_person: PerPerson[];
  unassigned_items: string[];
  totals_reconcile: boolean;
  // Where the numbers came from (P1.3). Backend sets one of backend_live_orders /
  // backend_session_draft; the local calculator sets local_demo_preview.
  split_source?: string;
};

// Honest, human label for the split's provenance. Prefers the backend's
// split_source; falls back to the coarse backend/local origin.
export function splitSourceLabel(
  split: MealSplit | null | undefined,
  source: "backend" | "local",
): string {
  switch (split?.split_source) {
    case "backend_live_orders":
      return "Calculated by Orkestr backend";
    case "backend_session_draft":
      return "Draft itemized split";
    case "local_demo_preview":
      return "Demo split preview";
    case "fallback_demo":
      return "Local fallback";
    default:
      return source === "backend" ? "Calculated by Orkestr backend" : "Demo split preview";
  }
}

// The persistent, unmissable label shown on any item list that is NOT the group's
// real captured order (a session draft, the local demo preview, a fallback). In a
// live room these stand-in dishes are suppressed entirely; in demo mode they may
// be shown for the feature showcase, but only under this label.
export const SAMPLE_ITEMS_LABEL = "Sample items — not this restaurant's menu";

// True when the split's item lines are a stand-in menu rather than the group's real
// captured order. Only backend_live_orders (recomputed from entered orders) is real;
// every other source — a session draft, the local demo preview, a fallback, or an
// unknown/missing source — is a sample that must never be shown as the restaurant's
// actual food in a live room.
export function isSampleSplit(split: MealSplit | null | undefined): boolean {
  return (split?.split_source ?? "") !== "backend_live_orders";
}

const DEFAULT_CURRENCY = "SGD";
const DEFAULT_SERVICE_CHARGE_RATE = 0.1;
const DEFAULT_GST_RATE = 0.09;

function toCents(amount: number): number {
  if (!Number.isFinite(amount)) return 0;
  return Math.round(amount * 100);
}

function money(cents: number): number {
  return Math.round(cents) / 100;
}

// Largest-remainder allocation: sums to totalCents EXACTLY, deterministic.
export function allocateCents(weights: number[], totalCents: number): number[] {
  const n = weights.length;
  if (n === 0) return [];
  totalCents = Math.round(totalCents);
  if (totalCents === 0) return new Array(n).fill(0);
  const sw = weights.reduce((a, b) => a + b, 0);
  if (sw <= 0) {
    const base = Math.floor(totalCents / n);
    const extra = totalCents - base * n;
    return Array.from({ length: n }, (_, i) => base + (i < extra ? 1 : 0));
  }
  const exact = weights.map((w) => (totalCents * w) / sw);
  const floors = exact.map((x) => Math.floor(x));
  const remainder = totalCents - floors.reduce((a, b) => a + b, 0);
  const order = exact
    .map((x, i) => ({ i, frac: x - Math.floor(x) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i);
  for (let k = 0; k < remainder; k += 1) floors[order[k].i] += 1;
  return floors;
}

function peopleFor(item: SplitItemInput): string[] {
  const category = (item.category || "").toLowerCase();
  let people = category === "shared" ? item.shared_by || [] : item.assigned_to || [];
  if (people.length === 0) people = item.shared_by || [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of people) {
    if (p && !seen.has(p)) {
      seen.add(p);
      out.push(p);
    }
  }
  return out;
}

export type ComputeInput = {
  currency?: string;
  service_charge_rate?: number;
  gst_rate?: number;
  members: SplitMemberInput[];
  items: SplitItemInput[];
  mode?: SplitMode;
};

export function computeSplit(input: ComputeInput, modeOverride?: SplitMode): MealSplit {
  const mode: SplitMode = (modeOverride || input.mode || "itemized") as SplitMode;
  const currency = input.currency || DEFAULT_CURRENCY;
  const scRate = input.service_charge_rate ?? DEFAULT_SERVICE_CHARGE_RATE;
  const gstRate = input.gst_rate ?? DEFAULT_GST_RATE;

  const memberIds = input.members.map((m) => m.member_id).filter(Boolean);
  const names: Record<string, string> = {};
  const budgets: Record<string, number> = {};
  input.members.forEach((m) => {
    names[m.member_id] = m.name ?? m.member_id;
    if (typeof m.budget === "number" && m.budget > 0) budgets[m.member_id] = m.budget;
  });
  const idx: Record<string, number> = {};
  memberIds.forEach((mid, i) => {
    idx[mid] = i;
  });

  const perSub = new Array(memberIds.length).fill(0);
  const perItems: Record<string, PersonItem[]> = {};
  memberIds.forEach((mid) => {
    perItems[mid] = [];
  });
  let subtotalCents = 0;
  const unassigned: string[] = [];

  for (const item of input.items) {
    const lineCents = toCents(item.price) * (item.quantity || 1);
    if (lineCents <= 0) continue;
    const category = (item.category || (item.shared_by?.length ? "shared" : "personal")).toLowerCase();
    const people = peopleFor(item).filter((p) => p in idx);
    if (people.length === 0) {
      unassigned.push(item.name || item.id || "item");
      continue;
    }
    subtotalCents += lineCents;
    const shares = allocateCents(new Array(people.length).fill(1), lineCents);
    people.forEach((person, i) => {
      perSub[idx[person]] += shares[i];
      perItems[person].push({
        id: item.id,
        name: item.name || "Item",
        amount: money(shares[i]),
        line_total: money(lineCents),
        quantity: item.quantity || 1,
        kind: category === "shared" ? "shared" : "personal",
        shared_with: category === "shared" ? people.map((p) => names[p] ?? p) : [],
        shared_count: category === "shared" ? people.length : 1,
      });
    });
  }

  let subtotals = perSub;
  if (mode === "equal" && memberIds.length > 0) {
    subtotals = allocateCents(new Array(memberIds.length).fill(1), subtotalCents);
  }

  const scTotalCents = Math.round(scRate * subtotalCents);
  const gstTotalCents = Math.round(gstRate * (subtotalCents + scTotalCents));
  const perSc = allocateCents([...subtotals], scTotalCents);
  const perGst = allocateCents(
    subtotals.map((s, i) => s + perSc[i]),
    gstTotalCents,
  );
  const totalCents = subtotalCents + scTotalCents + gstTotalCents;

  const perPerson: PerPerson[] = memberIds.map((mid, i) => {
    const pTotalCents = subtotals[i] + perSc[i] + perGst[i];
    const row: PerPerson = {
      member_id: mid,
      name: names[mid] ?? mid,
      items_subtotal: money(subtotals[i]),
      service_charge: money(perSc[i]),
      gst: money(perGst[i]),
      total: money(pTotalCents),
      status: pTotalCents > 0 ? "owes" : "settled",
      items: perItems[mid] ?? [],
      shared_items: (perItems[mid] ?? []).filter((it) => it.kind === "shared"),
    };
    if (mid in budgets) {
      row.budget = budgets[mid];
      const overCents = pTotalCents - toCents(budgets[mid]);
      if (overCents > 0) {
        row.over_budget = true;
        row.over_by = money(overCents);
        row.warning = `Over ${row.name}'s $${budgets[mid].toFixed(0)} budget by $${money(overCents).toFixed(2)}`;
      } else {
        row.over_budget = false;
      }
    }
    return row;
  });

  return {
    currency,
    mode,
    service_charge_rate: scRate,
    gst_rate: gstRate,
    subtotal: money(subtotalCents),
    service_charge: money(scTotalCents),
    gst: money(gstTotalCents),
    total: money(totalCents),
    rounding: money(totalCents - (subtotalCents + scTotalCents + gstTotalCents)),
    items: input.items,
    per_person: perPerson,
    unassigned_items: unassigned,
    totals_reconcile:
      perPerson.reduce((a, p) => a + toCents(p.total), 0) === totalCents,
  };
}

// Re-apply REAL per-member budgets (their stated budget_max) to an already-computed
// split, recomputing over_budget / over_by / warning per person. Works regardless of
// where the split came from (backend /api/split or the local demo calculator), so the
// UI flags every member whose computed total exceeds their OWN stated budget — not a
// hardcoded demo budget. A member absent from `budgets` (no real budget_max) has all
// budget fields cleared, so we never raise a false "over budget" flag.
export function applyMemberBudgets(
  split: MealSplit,
  budgets: Record<string, number>,
): MealSplit {
  const per_person: PerPerson[] = split.per_person.map((p) => {
    const budget = budgets[p.member_id];
    if (typeof budget !== "number" || !(budget > 0)) {
      // No real budget for this member — strip any demo budget flags.
      const { budget: _b, over_budget: _ob, over_by: _oby, warning: _w, ...rest } = p;
      return rest;
    }
    const overBy = Math.round((p.total - budget) * 100) / 100;
    if (overBy > 0) {
      return {
        ...p,
        budget,
        over_budget: true,
        over_by: overBy,
        warning: `Over ${p.name}'s $${budget.toFixed(0)} budget by $${overBy.toFixed(2)}`,
      };
    }
    const { over_by: _oby, warning: _w, ...rest } = p;
    return { ...rest, budget, over_budget: false };
  });
  return { ...split, per_person };
}

// ---- demo data (mirrors core/meal_split.py, so numbers match the backend) ----

export const DEMO_SPLIT_MEMBERS: SplitMemberInput[] = [
  { member_id: "P-001", name: "Alice", budget: 50 },
  { member_id: "P-002", name: "Bob", budget: 40 },
  { member_id: "P-003", name: "Carol", budget: 80 },
  { member_id: "P-004", name: "Dave", budget: 45 },
  { member_id: "P-005", name: "Eve", budget: 40 },
];

export const DEMO_SPLIT_ITEMS: SplitItemInput[] = [
  { id: "item-001", name: "Bibimbap (halal)", price: 18.0, quantity: 1, assigned_to: ["P-001"], shared_by: [], category: "personal" },
  { id: "item-002", name: "Iced barley", price: 4.5, quantity: 1, assigned_to: ["P-001"], shared_by: [], category: "personal" },
  { id: "item-003", name: "Vegetarian japchae", price: 16.0, quantity: 1, assigned_to: ["P-002"], shared_by: [], category: "personal" },
  { id: "item-004", name: "Grilled chicken (no raw fish)", price: 22.0, quantity: 1, assigned_to: ["P-003"], shared_by: [], category: "personal" },
  { id: "item-005", name: "Vegan buddha bowl", price: 17.5, quantity: 1, assigned_to: ["P-005"], shared_by: [], category: "personal" },
  { id: "item-006", name: "Korean BBQ platter", price: 60.0, quantity: 1, assigned_to: [], shared_by: ["P-001", "P-003", "P-004"], category: "shared" },
  { id: "item-007", name: "Kimchi & sides", price: 12.0, quantity: 1, assigned_to: [], shared_by: ["P-001", "P-002", "P-003", "P-004", "P-005"], category: "shared" },
];

export function demoSplit(mode: SplitMode = "itemized", members: SplitMemberInput[] = DEMO_SPLIT_MEMBERS): MealSplit {
  const split = computeSplit(
    {
      currency: DEFAULT_CURRENCY,
      service_charge_rate: DEFAULT_SERVICE_CHARGE_RATE,
      gst_rate: DEFAULT_GST_RATE,
      members,
      items: DEMO_SPLIT_ITEMS,
      mode,
    },
    mode,
  );
  // Local calculator — honestly labelled so it's never shown as real backend data.
  split.split_source = "local_demo_preview";
  return split;
}

export function fmtMoney(value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "$--";
}
