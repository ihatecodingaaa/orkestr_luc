#!/usr/bin/env bash
#
# fire_receipt.sh — Presenter-only. Fire ONE real Base-Sepolia $0.001 USDC x402
# proof and print the fresh tx hash + Basescan URL to read on stage.
#
# Run this ONCE, immediately before presenting. It is deliberately manual: it is
# the ONLY thing that triggers a real on-chain payment on the demo-receipt path.
# The normal app flow, page loads, and polling never call it.
#
# Requirements: curl only (no jq, no other deps). The backend must be reachable
# and configured with the demo-receipt env vars (see the env checklist), i.e.
# ENABLE_X402_DEMO_RECEIPT=true and a funded buyer wallet on Railway.
#
# Usage:
#   BASE_URL=https://<your-app>.up.railway.app \
#   DEMO_SECRET='<the X402_DEMO_RECEIPT_SECRET you set>' \
#   bash scripts/fire_receipt.sh
#
# Or edit the two placeholders below and just run: bash scripts/fire_receipt.sh
# ---------------------------------------------------------------------------

set -euo pipefail

# ----- FILL THESE IN (or pass as env vars) ---------------------------------
BASE_URL="${BASE_URL:-http://localhost:8000}"          # <-- your backend URL
DEMO_SECRET="${DEMO_SECRET:-REPLACE_WITH_X402_DEMO_RECEIPT_SECRET}"  # <-- must equal X402_DEMO_RECEIPT_SECRET on the server
CONFIRM_PHRASE="PAY_0.001_USDC"                        # exact confirm phrase; do not change
# ---------------------------------------------------------------------------

BASE_URL="${BASE_URL%/}"   # strip any trailing slash

if [ "${DEMO_SECRET}" = "REPLACE_WITH_X402_DEMO_RECEIPT_SECRET" ]; then
  echo "ERROR: set DEMO_SECRET (env var or edit the placeholder in this script)." >&2
  echo "       It must exactly match X402_DEMO_RECEIPT_SECRET on the backend." >&2
  exit 2
fi

echo "Firing ONE real x402 proof against ${BASE_URL} ..."
echo "  POST /api/x402/demo-receipt/run  (confirm=${CONFIRM_PHRASE})"
echo "-----------------------------------------------------------------"

# -s silent, -S show errors, -w capture the HTTP status on the last line.
RESPONSE="$(curl -sS -w $'\n%{http_code}' \
  -X POST "${BASE_URL}/api/x402/demo-receipt/run" \
  -H "Content-Type: application/json" \
  -H "X-Orkestr-Demo-Secret: ${DEMO_SECRET}" \
  -d "{\"confirm\":\"${CONFIRM_PHRASE}\"}")"

HTTP_CODE="$(printf '%s' "${RESPONSE}" | tail -n1)"
BODY="$(printf '%s' "${RESPONSE}" | sed '$d')"

echo "HTTP ${HTTP_CODE}"
echo "${BODY}"
echo "-----------------------------------------------------------------"

# Extract fields with plain grep/sed (no jq). null-safe: empty if absent.
extract() {  # $1 = json key
  printf '%s' "${BODY}" \
    | grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
    | head -n1 \
    | sed -E "s/.*:[[:space:]]*\"([^\"]*)\"/\1/"
}

REAL_TX_HASH="$(extract real_tx_hash)"
EXPLORER_URL="$(extract explorer_url)"
PAYMENT_MODE="$(extract payment_mode)"
FALLBACK_REASON="$(extract fallback_reason)"

echo "payment_mode : ${PAYMENT_MODE:-<none>}"
if [ -n "${FALLBACK_REASON}" ]; then
  echo "fallback_reason: ${FALLBACK_REASON}"
fi
echo "real_tx_hash : ${REAL_TX_HASH:-<null>}"
echo "explorer_url : ${EXPLORER_URL:-<null>}"
echo "================================================================="

if [ "${HTTP_CODE}" = "200" ] && [ "${PAYMENT_MODE}" = "live" ] && [ -n "${REAL_TX_HASH}" ]; then
  # Prefer the explorer_url the backend built; fall back to constructing it.
  SHARE_URL="${EXPLORER_URL:-https://sepolia.basescan.org/tx/${REAL_TX_HASH}}"
  echo "LIVE PROOF — share this on stage:"
  echo "  ${SHARE_URL}"
  exit 0
fi

echo "NOTE: this was NOT a fresh live proof (payment_mode=${PAYMENT_MODE:-<none>})."
echo "  - 'mock'  => the run timed out/errored and fell back to the mock stamp."
echo "  - 403/404 => gate rejected (check DEMO_SECRET and ENABLE_X402_DEMO_RECEIPT)."
echo "  - 429     => cooldown active; wait and retry."
echo "  - 502     => live payment did not settle; check server logs / wallet funding."
exit 1
