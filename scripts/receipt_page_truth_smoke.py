"""Smoke for the shareable /receipt/{sid} HTML page product-truth (UI/API only).

Every x402 receipt surface must obey ONE rule: the CURRENT payment proof is a
fresh real tx only; a saved receipt is a labelled PREVIOUS proof; a mock is never
shown as a real receipt. This drives the server-rendered share page through all
three receipt states (saved / fresh / mock) by monkeypatching latest_receipt()
and asserting the emitted HTML. In-process TestClient — no network, no signer, no
payment (paidpath_guard forces every spend flag off).

Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

from fastapi.testclient import TestClient

import payments.x402_demo_receipt as XDR
from api.main import app

client = TestClient(app)
_failures = []

SAVED_TX = XDR.SAVED_RECEIPT["txHash"]
FRESH_TX = "0x" + "ab" * 32
MOCK_TX = "0x" + "cd" * 32
FORBIDDEN = ["evm_private_key", "x402_demo_receipt_secret", "private key", "mnemonic"]


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def receipt_html(fake_latest):
    """Render /receipt/ORK-001 with latest_receipt() patched to fake_latest."""
    orig = XDR.latest_receipt
    XDR.latest_receipt = lambda: dict(fake_latest)
    try:
        r = client.get("/receipt/ORK-001")
    finally:
        XDR.latest_receipt = orig
    return r


FRESH_RECEIPT = {
    "payment_mode": "x402_real", "source": XDR.FRESH_SOURCE, "label": XDR.FRESH_LABEL,
    "network": "eip155:84532", "chain": "Base Sepolia",
    "asset": XDR.SAVED_RECEIPT["asset"], "amount": 1000, "amount_display": "0.001 USDC",
    "buyer": XDR.SAVED_RECEIPT["buyer"], "merchant": XDR.SAVED_RECEIPT["merchant"],
    "txHash": FRESH_TX, "blockExplorerUrl": "https://sepolia.basescan.org/tx/" + FRESH_TX,
    "created_at": "2026-07-11T10:00:00+00:00",
}
MOCK_RECEIPT = {
    "payment_mode": "mock", "source": "mock_fallback", "label": "Mock fallback receipt",
    "amount": 1000, "amount_display": "0.001 USDC",
    "txHash": MOCK_TX, "blockExplorerUrl": None, "real_tx_hash": None, "explorer_url": None,
    "fallback_reason": "timeout",
}

print("Receipt page product-truth smoke (/receipt/{sid})")
print("=" * 60)

# 1. SAVED fallback -> NOT current; shown only as a labelled PREVIOUS proof.
r = receipt_html(dict(XDR.SAVED_RECEIPT))
h = r.text
check("saved: page 200", r.status_code == 200, str(r.status_code))
check("saved: says 'No current x402 payment receipt'",
      "No current x402 payment receipt" in h)
check("saved: labelled 'Previous verified testnet proof'",
      "Previous verified testnet proof" in h)
check("saved: clarifies it is NOT the current session payment",
      "not the current session payment" in h)
check("saved: does NOT claim a fresh/current receipt",
      "Fresh x402 testnet receipt" not in h)
check("saved: prior tx shown only in the previous-proof section (Basescan present)",
      "sepolia.basescan.org/tx/" + SAVED_TX in h)

# 2. FRESH real receipt -> current proof with tx + basescan + amount + timestamp.
r = receipt_html(FRESH_RECEIPT)
h = r.text
check("fresh: page 200", r.status_code == 200, str(r.status_code))
check("fresh: says 'Fresh x402 testnet receipt'", "Fresh x402 testnet receipt" in h)
check("fresh: shows the fresh tx on Basescan",
      "sepolia.basescan.org/tx/" + FRESH_TX in h)
check("fresh: shows amount 0.001 USDC", "0.001 USDC" in h)
check("fresh: shows settled timestamp", "Settled 2026-07-11T10:00:00" in h)
check("fresh: does NOT say 'No current x402 payment receipt'",
      "No current x402 payment receipt" not in h)

# 3. MOCK fallback -> never a real receipt: no tx hash link, honest copy.
r = receipt_html(MOCK_RECEIPT)
h = r.text
check("mock: page 200", r.status_code == 200, str(r.status_code))
check("mock: says 'did not settle a real payment. No payment was sent.'",
      "did not settle a real payment. No payment was sent." in h)
check("mock: NO Basescan tx link for the mock hash",
      "sepolia.basescan.org/tx/" + MOCK_TX not in h)
check("mock: NO Basescan link at all", "sepolia.basescan.org/tx/" not in h)
check("mock: not labelled a fresh receipt", "Fresh x402 testnet receipt" not in h)

# 4. Participant safety: the shareable page never exposes a run control / secret.
r = receipt_html(dict(XDR.SAVED_RECEIPT))
h = r.text
check("page has NO run button", "Run fresh x402 proof" not in h)
check("page has NO secret input placeholder", "X402_DEMO_RECEIPT_SECRET" not in h)
check("page leaks no secret-ish material",
      all(bad not in h.lower() for bad in FORBIDDEN))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
