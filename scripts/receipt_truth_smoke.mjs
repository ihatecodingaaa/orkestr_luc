// Smoke for x402 receipt product-truth in live mode (UI semantics).
//
// The repo has no JS test runner, so this node-runnable check MIRRORS the two
// decision points in the frontend:
//   1. toReceipt()  (src/lib/orkestr-api.ts) — maps a backend body to a receipt,
//      rejecting mock/fallback bodies (null) so they can never render as proof.
//   2. the live-mode display predicate used by PaymentProofCard / TrustPanel:
//      awaitingFreshProof = liveMode && !receipt.isFresh  -> "not run yet".
// If you change either in the app, change it here too. Run:
//   node scripts/receipt_truth_smoke.mjs
//
// Asserts:
//  - a fresh_demo_receipt is the ONLY thing shown as current proof
//  - a saved_verified_receipt is never current in live mode (secondary/fallback)
//  - a mock_fallback / payment_mode=mock body is rejected (never renderable)
//  - live mode with no fresh receipt shows "Fresh x402 payment not run yet"

// ---- mirror of asString + toReceipt (only the branches that matter here) ----
function asString(v, dflt = "") {
  return typeof v === "string" ? v : dflt;
}

// MUST match toReceipt() in src/lib/orkestr-api.ts.
function toReceipt(raw) {
  if (raw === null || typeof raw !== "object") return null;
  if (asString(raw.source) === "mock_fallback" || asString(raw.payment_mode) === "mock") {
    return null; // mock/fallback is not a real settlement — never renderable
  }
  const source =
    asString(raw.source) === "fresh_demo_receipt" ? "fresh_demo_receipt" : "saved_verified_receipt";
  return {
    source,
    isFresh: source === "fresh_demo_receipt",
    txHash: asString(raw.txHash),
    createdAt: typeof raw.created_at === "string" ? raw.created_at : null,
  };
}

// MUST match the live-mode gate in PaymentProofCard / GroupRoom / ParticipantView.
function isCurrentProof(receipt, liveMode) {
  // In live mode, ONLY a fresh receipt is current proof; otherwise "not run yet".
  if (!receipt) return false;
  if (liveMode) return receipt.isFresh === true;
  return true; // demo mode may show the saved receipt (labelled honestly)
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

console.log("x402 receipt product-truth smoke (UI semantics)");
console.log("=".repeat(60));

const FRESH = {
  source: "fresh_demo_receipt",
  payment_mode: "x402_real",
  txHash: "0xfresh",
  created_at: "2026-07-11T10:00:00Z",
};
const SAVED = { source: "saved_verified_receipt", txHash: "0xsaved" };
const MOCK = { source: "mock_fallback", payment_mode: "mock", txHash: "0xmockPLACEHOLDER" };
const MOCK_MODE_ONLY = { payment_mode: "mock", txHash: "0xmock2" };

// 1. Mock bodies are rejected outright — never a renderable receipt.
check("mock_fallback source -> toReceipt null", toReceipt(MOCK) === null);
check("payment_mode=mock -> toReceipt null", toReceipt(MOCK_MODE_ONLY) === null);

// 2. Fresh is the only current proof in live mode.
const fresh = toReceipt(FRESH);
check("fresh_demo_receipt -> isFresh true", fresh !== null && fresh.isFresh === true);
check("fresh carries a timestamp (createdAt)", fresh?.createdAt === "2026-07-11T10:00:00Z");
check("live mode: fresh IS current proof", isCurrentProof(fresh, true) === true);

// 3. Saved receipt is NEVER current in live mode (only secondary/fallback).
const saved = toReceipt(SAVED);
check("saved_verified_receipt -> isFresh false", saved !== null && saved.isFresh === false);
check("live mode: saved is NOT current proof (shows 'not run yet')",
  isCurrentProof(saved, true) === false);
check("demo mode: saved may show (labelled)", isCurrentProof(saved, false) === true);

// 4. Mock never becomes current proof in any mode (it's null -> not renderable).
check("live mode: mock is NOT current proof", isCurrentProof(toReceipt(MOCK), true) === false);
check("demo mode: mock is still NOT renderable", isCurrentProof(toReceipt(MOCK), false) === false);

// 5. Live mode with no fresh (default saved fallback) -> "not run yet".
const awaitingFreshProof = (liveMode, receipt) => liveMode && !(receipt && receipt.isFresh);
check("live + saved -> awaitingFreshProof true ('not run yet')",
  awaitingFreshProof(true, saved) === true);
check("live + fresh -> awaitingFreshProof false", awaitingFreshProof(true, fresh) === false);

console.log("\n" + "=".repeat(60));
if (failures.length) {
  console.log(`  RESULT: ${failures.length} FAILURE(S)`);
  for (const f of failures) console.log(`    - ${f}`);
  process.exit(1);
}
console.log("  RESULT: ALL PASS");
process.exit(0);
