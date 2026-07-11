// Smoke for the live-constraint sub-header formatter (Fix 3).
//
// The repo has no JS test runner, so this is a tiny node-runnable check that
// MIRRORS `constraintSummary` in src/lib/demo.ts EXACTLY. If you change the
// formatter there, change it here too. Run: `node scripts/constraint_summary_smoke.mjs`.
//
// Asserts the sub-header is derived from a member's live submitted constraints
// (diet · up to $budget · days), agreeing with the badges on the same card, and
// that an empty/pending constraints object yields "" (the caller then shows the
// persona vibe fallback, never a fake constraint line).

const DIET_OPTIONS = [
  { id: "halal", label: "Halal" },
  { id: "vegetarian", label: "Vegetarian" },
  { id: "no_raw_fish", label: "No raw fish" },
  { id: "vegan", label: "Vegan" },
];

// MUST match constraintSummary() in src/lib/demo.ts.
function constraintSummary(c) {
  const diet = c.dietary.map((id) => DIET_OPTIONS.find((d) => d.id === id)?.label ?? id);
  const parts = [];
  if (diet.length) parts.push(diet.join(" · "));
  if (Number(c.budget) > 0) parts.push(`up to $${c.budget}`);
  if (c.days.length) parts.push(c.days.join("/"));
  return parts.join(" · ");
}

const failures = [];
function check(label, ok, detail = "") {
  if (ok) {
    console.log(`  [PASS] ${label}`);
  } else {
    console.log(`  [FAIL] ${label}${detail ? ` — ${detail}` : ""}`);
    failures.push(label);
  }
}

console.log("Constraint summary smoke (Fix 3)");
console.log("=".repeat(60));

// 1. Full submitted constraints -> diet · up to $budget · days.
const alice = { days: ["FRI"], budget: "60", dietary: ["halal"], freeform: "" };
check(
  "single diet/budget/day formats as 'Halal · up to $60 · FRI'",
  constraintSummary(alice) === "Halal · up to $60 · FRI",
  constraintSummary(alice),
);

// 2. A DIFFERENT real submission produces a DIFFERENT sub-header (not the persona).
const guest = { days: ["SAT"], budget: "20", dietary: ["vegan"], freeform: "" };
check(
  "vegan / $20 / SAT reflects the real pick",
  constraintSummary(guest) === "Vegan · up to $20 · SAT",
  constraintSummary(guest),
);

// 3. Multiple diets + multiple days join cleanly.
const multi = { days: ["FRI", "SAT"], budget: "40", dietary: ["halal", "vegetarian"], freeform: "" };
check(
  "multi diet + multi day",
  constraintSummary(multi) === "Halal · Vegetarian · up to $40 · FRI/SAT",
  constraintSummary(multi),
);

// 4. Pending / empty -> "" so the caller falls back to the persona vibe.
check("empty constraints -> '' (pending fallback)",
  constraintSummary({ days: [], budget: "", dietary: [], freeform: "" }) === "");
check("blank/zero budget is not shown as '$0'",
  constraintSummary({ days: ["FRI"], budget: "0", dietary: [], freeform: "" }) === "FRI");

console.log("\n" + "=".repeat(60));
if (failures.length) {
  console.log(`  RESULT: ${failures.length} FAILURE(S)`);
  for (const f of failures) console.log(`    - ${f}`);
  process.exit(1);
}
console.log("  RESULT: ALL PASS");
process.exit(0);
